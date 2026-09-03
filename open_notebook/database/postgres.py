"""PostgreSQL storage primitives for the dockerless runtime.

The migration deliberately keeps the externally visible ``table:key`` IDs while
moving persistence to PostgreSQL. Domain-specific repositories can be moved to
native tables incrementally; this module provides the common pool, bootstrap and
generic record/relation primitives needed during that transition.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, cast
from uuid import uuid4

from loguru import logger
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from open_notebook.database.record_id import RecordID

_DEFAULT_DATABASE_URL = (
    "postgresql://open_notebook:open_notebook@localhost:5432/open_notebook"
)
_pool: AsyncConnectionPool | None = None
_schema_ready = False


def get_database_url() -> str:
    return os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or _DEFAULT_DATABASE_URL


def _json_default(value: Any) -> Any:
    if isinstance(value, RecordID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Cannot JSON encode {type(value).__name__}")


def normalize_json(value: Any) -> Any:
    """Return a JSON-compatible copy suitable for JSONB parameters."""
    return json.loads(json.dumps(value, default=_json_default))


async def get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=get_database_url(),
            min_size=1,
            max_size=max(2, int(os.getenv("OPEN_NOTEBOOK_DB_POOL_SIZE", "10"))),
            open=False,
            kwargs={"row_factory": dict_row},
        )
        await _pool.open()
    return _pool


@asynccontextmanager
async def db_connection() -> AsyncIterator[AsyncConnection[DictRow]]:
    pool = await get_pool()
    async with pool.connection() as connection:
        yield cast(AsyncConnection[DictRow], connection)


async def close_pool() -> None:
    global _pool, _schema_ready
    if _pool is not None:
        await _pool.close()
        _pool = None
    _schema_ready = False


async def ensure_schema() -> None:
    """Create the PostgreSQL foundation schema idempotently."""
    global _schema_ready
    if _schema_ready:
        return

    statements = (
        "CREATE EXTENSION IF NOT EXISTS vector",
        """
        CREATE TABLE IF NOT EXISTS on_record (
            table_name text NOT NULL,
            record_key text NOT NULL,
            data jsonb NOT NULL DEFAULT '{}'::jsonb,
            created timestamptz NOT NULL DEFAULT now(),
            updated timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (table_name, record_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS on_record_table_updated_idx "
        "ON on_record(table_name, updated DESC)",
        """
        CREATE TABLE IF NOT EXISTS on_relation (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            kind text NOT NULL,
            source_table text NOT NULL,
            source_key text NOT NULL,
            target_table text NOT NULL,
            target_key text NOT NULL,
            data jsonb NOT NULL DEFAULT '{}'::jsonb,
            created timestamptz NOT NULL DEFAULT now(),
            UNIQUE(kind, source_table, source_key, target_table, target_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS on_relation_source_idx "
        "ON on_relation(kind, source_table, source_key)",
        "CREATE INDEX IF NOT EXISTS on_relation_target_idx "
        "ON on_relation(kind, target_table, target_key)",
        """
        CREATE TABLE IF NOT EXISTS source_embedding_pg (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            source_key text NOT NULL,
            order_index integer NOT NULL,
            content text NOT NULL,
            embedding vector,
            created timestamptz NOT NULL DEFAULT now(),
            UNIQUE(source_key, order_index)
        )
        """,
        "CREATE INDEX IF NOT EXISTS source_embedding_pg_source_idx "
        "ON source_embedding_pg(source_key, order_index)",
        """
        CREATE TABLE IF NOT EXISTS record_embedding_pg (
            table_name text NOT NULL,
            record_key text NOT NULL,
            content text NOT NULL,
            embedding vector NOT NULL,
            updated timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(table_name, record_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS record_embedding_pg_table_idx "
        "ON record_embedding_pg(table_name, record_key)",
        """
        CREATE TABLE IF NOT EXISTS command_job (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            app text NOT NULL,
            command_name text NOT NULL,
            input jsonb NOT NULL,
            status text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','completed','failed')),
            attempt integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 1,
            run_after timestamptz NOT NULL DEFAULT now(),
            lease_until timestamptz,
            worker_id text,
            result jsonb,
            error_message text,
            created timestamptz NOT NULL DEFAULT now(),
            updated timestamptz NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS command_job_claim_idx "
        "ON command_job(status, run_after, lease_until, created)",
        """
        CREATE TABLE IF NOT EXISTS schema_migration (
            version integer PRIMARY KEY,
            name text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """,
    )

    async with db_connection() as connection:
        async with connection.cursor() as cursor:
            for statement in statements:
                await cursor.execute(statement)
        await connection.commit()
    _schema_ready = True
    logger.debug("PostgreSQL foundation schema is ready")


async def create_record(
    table: str, data: Mapping[str, Any], record_key: str | None = None
) -> dict[str, Any]:
    await ensure_schema()
    key = record_key or str(uuid4())
    now = datetime.now(timezone.utc)
    payload = normalize_json({k: v for k, v in data.items() if k != "id"})
    payload.setdefault("created", now.isoformat())
    payload["updated"] = now.isoformat()
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                """
                INSERT INTO on_record(table_name, record_key, data, created, updated)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                RETURNING table_name, record_key, data, created, updated
                """,
                (table, key, json.dumps(payload), now, now),
            )
        ).fetchone()
        await connection.commit()
    if row is None:
        raise RuntimeError("PostgreSQL did not return the created record")
    return record_row(row)


async def get_record(record_id: RecordID) -> dict[str, Any] | None:
    await ensure_schema()
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                "SELECT table_name, record_key, data, created, updated "
                "FROM on_record WHERE table_name=%s AND record_key=%s",
                (record_id.table, record_id.id),
            )
        ).fetchone()
    return record_row(row) if row else None


async def list_records(table: str) -> list[dict[str, Any]]:
    await ensure_schema()
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                "SELECT table_name, record_key, data, created, updated "
                "FROM on_record WHERE table_name=%s",
                (table,),
            )
        ).fetchall()
    return [record_row(row) for row in rows]


async def update_record(
    record_id: RecordID, data: Mapping[str, Any], *, upsert: bool = False
) -> dict[str, Any] | None:
    await ensure_schema()
    payload = normalize_json({k: v for k, v in data.items() if k != "id"})
    now = datetime.now(timezone.utc)
    payload["updated"] = now.isoformat()
    async with db_connection() as connection:
        if upsert:
            row = await (
                await connection.execute(
                    """
                    INSERT INTO on_record(table_name, record_key, data, created, updated)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT(table_name, record_key) DO UPDATE
                    SET data = on_record.data || EXCLUDED.data, updated = EXCLUDED.updated
                    RETURNING table_name, record_key, data, created, updated
                    """,
                    (
                        record_id.table,
                        record_id.id,
                        json.dumps(payload),
                        now,
                        now,
                    ),
                )
            ).fetchone()
        else:
            row = await (
                await connection.execute(
                    """
                    UPDATE on_record SET data = data || %s::jsonb, updated=%s
                    WHERE table_name=%s AND record_key=%s
                    RETURNING table_name, record_key, data, created, updated
                    """,
                    (json.dumps(payload), now, record_id.table, record_id.id),
                )
            ).fetchone()
        await connection.commit()
    return record_row(row) if row else None


async def delete_record(record_id: RecordID) -> bool:
    await ensure_schema()
    async with db_connection() as connection:
        if record_id.table == "source":
            source_id = str(record_id)
            # Remove dependent vector rows before deleting insight records.
            await connection.execute(
                """
                DELETE FROM record_embedding_pg
                WHERE table_name='source_insight'
                  AND record_key IN (
                      SELECT record_key FROM on_record
                      WHERE table_name='source_insight' AND data->>'source'=%s
                  )
                """,
                (source_id,),
            )
            await connection.execute(
                "DELETE FROM on_record "
                "WHERE table_name='source_insight' AND data->>'source'=%s",
                (source_id,),
            )
            await connection.execute(
                "DELETE FROM source_embedding_pg WHERE source_key=%s",
                (record_id.id,),
            )

        await connection.execute(
            "DELETE FROM record_embedding_pg WHERE table_name=%s AND record_key=%s",
            (record_id.table, record_id.id),
        )
        cursor = await connection.execute(
            "DELETE FROM on_record WHERE table_name=%s AND record_key=%s",
            (record_id.table, record_id.id),
        )
        await connection.execute(
            "DELETE FROM on_relation "
            "WHERE (source_table=%s AND source_key=%s) "
            "OR (target_table=%s AND target_key=%s)",
            (record_id.table, record_id.id, record_id.table, record_id.id),
        )
        await connection.commit()
        return bool(cursor.rowcount)


async def relate(
    source: RecordID,
    kind: str,
    target: RecordID,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    await ensure_schema()
    payload = normalize_json(data or {})
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                """
                INSERT INTO on_relation(
                    kind, source_table, source_key, target_table, target_key, data
                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT(kind, source_table, source_key, target_table, target_key)
                DO UPDATE SET data=EXCLUDED.data
                RETURNING *
                """,
                (
                    kind,
                    source.table,
                    source.id,
                    target.table,
                    target.id,
                    json.dumps(payload),
                ),
            )
        ).fetchone()
        await connection.commit()
    if row is None:
        raise RuntimeError("PostgreSQL did not return the relation")
    return relation_row(row)


def record_row(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row.get("data") or {})
    data["id"] = f"{row['table_name']}:{row['record_key']}"
    data.setdefault("created", row["created"])
    data["updated"] = row["updated"]
    return data


def relation_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{row['kind']}:{row['id']}",
        "in": f"{row['source_table']}:{row['source_key']}",
        "out": f"{row['target_table']}:{row['target_key']}",
        **dict(row.get("data") or {}),
    }
