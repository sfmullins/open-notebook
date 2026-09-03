"""PostgreSQL storage primitives for the dockerless runtime.

The migration deliberately keeps the externally visible ``table:key`` IDs while
moving persistence to PostgreSQL. Domain-specific repositories can be moved to
native tables incrementally; this module provides the common pool, bootstrap and
generic record/relation primitives needed during that transition.

The public connection surface intentionally retains the small cursor/result API
used by the repository layer while the underlying driver is asyncpg. This keeps
the PostgreSQL migration isolated from domain code and avoids redistributing an
LGPL PostgreSQL client in the Vält application layer.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Mapping, Sequence
from uuid import uuid4

import asyncpg
from loguru import logger

from open_notebook.database.record_id import RecordID

_DEFAULT_DATABASE_URL = (
    "postgresql://open_notebook:open_notebook@localhost:5432/open_notebook"
)
_pools: dict[asyncio.AbstractEventLoop, asyncpg.Pool] = {}
_pool_tasks: dict[asyncio.AbstractEventLoop, asyncio.Task[asyncpg.Pool]] = {}
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


def _json_encoder(value: Any) -> str:
    # Existing repository calls deliberately pass pre-serialized JSON strings
    # to explicit ::json/::jsonb casts. Preserve those while also supporting
    # native dict/list values passed by future asyncpg-native callers.
    if isinstance(value, str):
        return value
    return json.dumps(value, default=_json_default)


async def _init_connection(connection: asyncpg.Connection) -> None:
    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(
            type_name,
            schema="pg_catalog",
            encoder=_json_encoder,
            decoder=json.loads,
            format="text",
        )


class _Result:
    """Minimal async cursor result compatible with the repository call sites."""

    def __init__(self, rows: Sequence[Mapping[str, Any]] = (), rowcount: int = 0) -> None:
        self._rows = [dict(row) for row in rows]
        self.rowcount = rowcount

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


def _convert_placeholders(statement: str, parameter_count: int) -> str:
    """Convert the repository's DB-API ``%s`` placeholders to asyncpg ``$n``."""
    index = 0

    def replacement(_: re.Match[str]) -> str:
        nonlocal index
        index += 1
        return f"${index}"

    converted = re.sub(r"%s", replacement, statement)
    if index != parameter_count:
        raise ValueError(
            f"SQL placeholder mismatch: statement has {index} placeholders, "
            f"but {parameter_count} parameters were supplied"
        )
    return converted


def _command_rowcount(tag: str) -> int:
    for token in reversed(tag.split()):
        if token.isdigit():
            return int(token)
    return 0


class _CursorAdapter:
    def __init__(self, connection: "_ConnectionAdapter") -> None:
        self._connection = connection
        self._result = _Result()

    async def __aenter__(self) -> "_CursorAdapter":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(
        self, statement: str, params: Sequence[Any] | None = None
    ) -> _Result:
        self._result = await self._connection.execute(statement, params)
        return self._result

    async def fetchone(self) -> dict[str, Any] | None:
        return await self._result.fetchone()

    async def fetchall(self) -> list[dict[str, Any]]:
        return await self._result.fetchall()


class _ConnectionAdapter:
    """Transaction-aware compatibility facade over one asyncpg connection."""

    def __init__(self, connection: asyncpg.Connection) -> None:
        self._connection = connection
        self._transaction: asyncpg.Transaction | None = None

    async def begin(self) -> None:
        if self._transaction is None:
            self._transaction = self._connection.transaction()
            await self._transaction.start()

    async def _ensure_transaction(self) -> None:
        if self._transaction is None:
            await self.begin()

    async def execute(
        self, statement: str, params: Sequence[Any] | None = None
    ) -> _Result:
        await self._ensure_transaction()
        values = tuple(params or ())
        query = _convert_placeholders(statement, len(values))
        normalized = query.lstrip().lower()
        returns_rows = (
            normalized.startswith(("select", "show", "values", "explain", "with"))
            or " returning " in normalized
            or normalized.rstrip().endswith(" returning *")
        )
        if returns_rows:
            rows = await self._connection.fetch(query, *values)
            return _Result(rows, len(rows))
        tag = await self._connection.execute(query, *values)
        return _Result(rowcount=_command_rowcount(tag))

    def cursor(self) -> _CursorAdapter:
        return _CursorAdapter(self)

    async def commit(self) -> None:
        if self._transaction is not None:
            transaction = self._transaction
            self._transaction = None
            await transaction.commit()

    async def rollback(self) -> None:
        if self._transaction is not None:
            transaction = self._transaction
            self._transaction = None
            await transaction.rollback()


async def _create_pool() -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=get_database_url(),
        min_size=1,
        max_size=max(2, int(os.getenv("OPEN_NOTEBOOK_DB_POOL_SIZE", "10"))),
        init=_init_connection,
    )
    if pool is None:
        raise RuntimeError("asyncpg did not create a PostgreSQL connection pool")
    return pool


def _prune_closed_loop_pools() -> None:
    """Forget pools whose owning event loops have already been closed.

    asyncpg pools are event-loop-bound. The application has a long-lived API loop
    plus short-lived compatibility loops created by synchronous command callers,
    while pytest may create a new loop per test. Keeping one process-global pool
    therefore risks handing loop-bound transports to another loop. Once an owner
    loop is closed, asyncpg transport cleanup cannot safely call back into that
    loop, so this registry must only release its references. Live-loop shutdowns
    are drained normally by ``close_pool`` before their loop is closed.
    """
    for owner_loop in list(_pools):
        if owner_loop.is_closed():
            _pools.pop(owner_loop, None)
    for owner_loop in list(_pool_tasks):
        if owner_loop.is_closed():
            _pool_tasks.pop(owner_loop, None)


async def get_pool() -> asyncpg.Pool:
    loop = asyncio.get_running_loop()
    _prune_closed_loop_pools()

    pool = _pools.get(loop)
    if pool is not None:
        return pool

    task = _pool_tasks.get(loop)
    if task is None:
        task = loop.create_task(_create_pool())
        _pool_tasks[loop] = task

    try:
        pool = await task
    except BaseException:
        if _pool_tasks.get(loop) is task:
            _pool_tasks.pop(loop, None)
        raise

    if _pool_tasks.get(loop) is task:
        _pool_tasks.pop(loop, None)
    _pools[loop] = pool
    return pool


@asynccontextmanager
async def db_connection() -> AsyncIterator[_ConnectionAdapter]:
    pool = await get_pool()
    async with pool.acquire() as raw_connection:
        connection = _ConnectionAdapter(raw_connection)
        await connection.begin()
        try:
            yield connection
        except BaseException:
            await connection.rollback()
            raise
        finally:
            # Read-only call sites intentionally do not commit. Rolling back an
            # otherwise clean transaction simply releases its snapshot before
            # the pooled connection is returned.
            await connection.rollback()


async def close_pool() -> None:
    """Close the pool owned by the current event loop.

    Other loops may legitimately own independent pools in the same process, so a
    shutdown in one loop must not tear down live connections belonging to another.
    """
    global _schema_ready
    loop = asyncio.get_running_loop()

    task = _pool_tasks.pop(loop, None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    pool = _pools.pop(loop, None)
    if pool is not None:
        await pool.close()

    _prune_closed_loop_pools()
    if not _pools and not _pool_tasks:
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
