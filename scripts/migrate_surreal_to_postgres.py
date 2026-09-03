#!/usr/bin/env python3
"""One-time SurrealDB -> PostgreSQL data importer.

The importer talks to the old SurrealDB HTTP SQL endpoint directly, so the
SurrealDB Python client is not a runtime or migration dependency. Run this while
the old database is still available and the new PostgreSQL database is empty.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from loguru import logger

from open_notebook.database.embeddings import upsert_record_embedding
from open_notebook.database.postgres import db_connection, ensure_schema, normalize_json
from open_notebook.database.record_id import RecordID


def surreal_http_url(value: str) -> str:
    parsed = urlparse(value)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path
    if path.endswith("/rpc"):
        path = path[:-4]
    path = path.rstrip("/") + "/sql"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


class SurrealReader:
    def __init__(
        self, url: str, user: str, password: str, namespace: str, database: str
    ) -> None:
        self.url = surreal_http_url(url)
        self.auth = (user, password)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "text/plain",
            "surreal-ns": namespace,
            "surreal-db": database,
        }

    async def query(self, sql: str) -> Any:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                self.url,
                content=sql.encode("utf-8"),
                headers=self.headers,
                auth=self.auth,
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise RuntimeError("Unexpected SurrealDB SQL response")
        statement = payload[-1]
        if statement.get("status") != "OK":
            raise RuntimeError(str(statement.get("result") or "SurrealDB query failed"))
        return statement.get("result")

    async def tables(self) -> list[str]:
        info = await self.query("INFO FOR DB;")
        if not isinstance(info, Mapping):
            raise RuntimeError("INFO FOR DB did not return database metadata")
        tables = info.get("tables") or {}
        if isinstance(tables, Mapping):
            return sorted(str(name) for name in tables)
        raise RuntimeError("Could not enumerate SurrealDB tables")


async def postgres_is_empty() -> bool:
    await ensure_schema()
    async with db_connection() as connection:
        record_count = (
            await (await connection.execute("SELECT count(*) AS n FROM on_record")).fetchone()
        )["n"]
        relation_count = (
            await (
                await connection.execute("SELECT count(*) AS n FROM on_relation")
            ).fetchone()
        )["n"]
        source_embedding_count = (
            await (
                await connection.execute("SELECT count(*) AS n FROM source_embedding_pg")
            ).fetchone()
        )["n"]
    return (
        int(record_count) == 0
        and int(relation_count) == 0
        and int(source_embedding_count) == 0
    )


def clean_record(row: Mapping[str, Any]) -> tuple[RecordID, dict[str, Any]]:
    record_id = RecordID.parse(str(row["id"]))
    data = normalize_json({key: value for key, value in row.items() if key != "id"})
    # Old queue rows cannot be resumed by the PostgreSQL queue. Preserve user
    # data but deliberately sever stale processing references.
    if "command" in data:
        data["command"] = None
    return record_id, data


async def insert_record(record_id: RecordID, data: Mapping[str, Any]) -> None:
    created = data.get("created")
    updated = data.get("updated")
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO on_record(table_name, record_key, data, created, updated)
            VALUES (
                %s, %s, %s::jsonb,
                COALESCE(%s::timestamptz, now()),
                COALESCE(%s::timestamptz, now())
            )
            ON CONFLICT(table_name, record_key) DO UPDATE
            SET data=EXCLUDED.data, created=EXCLUDED.created, updated=EXCLUDED.updated
            """,
            (
                record_id.table,
                record_id.id,
                json.dumps(normalize_json(data)),
                str(created) if created else None,
                str(updated) if updated else None,
            ),
        )
        await connection.commit()


async def insert_relation(kind: str, row: Mapping[str, Any]) -> None:
    source = RecordID.parse(str(row["in"]))
    target = RecordID.parse(str(row["out"]))
    data = normalize_json(
        {key: value for key, value in row.items() if key not in {"id", "in", "out"}}
    )
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO on_relation(
                kind, source_table, source_key, target_table, target_key, data
            ) VALUES (%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT(kind, source_table, source_key, target_table, target_key)
            DO UPDATE SET data=EXCLUDED.data
            """,
            (
                kind,
                source.table,
                source.id,
                target.table,
                target.id,
                json.dumps(data),
            ),
        )
        await connection.commit()


async def insert_source_embedding(row: Mapping[str, Any]) -> None:
    source = RecordID.parse(str(row["source"]))
    embedding = row.get("embedding") or []
    vector_literal = "[" + ",".join(str(float(value)) for value in embedding) + "]"
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO source_embedding_pg(source_key, order_index, content, embedding)
            VALUES (%s,%s,%s,%s::vector)
            ON CONFLICT(source_key, order_index) DO UPDATE
            SET content=EXCLUDED.content, embedding=EXCLUDED.embedding
            """,
            (
                source.id,
                int(row.get("order") or 0),
                str(row.get("content") or ""),
                vector_literal,
            ),
        )
        await connection.commit()


async def migrate_record_vector(record_id: RecordID, raw: Mapping[str, Any]) -> bool:
    """Materialize legacy note/insight vectors into the pgvector search table."""
    if record_id.table not in {"note", "source_insight"}:
        return False
    embedding = raw.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        return False
    content = str(raw.get("content") or "")
    await upsert_record_embedding(record_id, content, embedding)
    return True


async def migrate(reader: SurrealReader, *, allow_nonempty: bool) -> None:
    await ensure_schema()
    if not allow_nonempty and not await postgres_is_empty():
        raise RuntimeError(
            "PostgreSQL target is not empty. Refusing to merge stores; use "
            "--allow-nonempty only for a deliberate retry."
        )

    tables = await reader.tables()
    logger.info(f"Found {len(tables)} SurrealDB tables")
    totals = {
        "records": 0,
        "relations": 0,
        "source_embeddings": 0,
        "record_embeddings": 0,
        "skipped": 0,
    }

    for table in tables:
        rows = await reader.query(f"SELECT * FROM `{table}`;")
        if not isinstance(rows, list):
            logger.warning(f"Skipping {table}: query did not return rows")
            continue
        logger.info(f"Migrating {table}: {len(rows)} rows")
        for raw in rows:
            if not isinstance(raw, Mapping) or "id" not in raw:
                totals["skipped"] += 1
                continue
            if table == "command":
                # Historical processing jobs are operational state, not user data.
                totals["skipped"] += 1
                continue
            if table == "source_embedding":
                await insert_source_embedding(raw)
                totals["source_embeddings"] += 1
            elif "in" in raw and "out" in raw:
                await insert_relation(table, raw)
                totals["relations"] += 1
            else:
                record_id, data = clean_record(raw)
                await insert_record(record_id, data)
                totals["records"] += 1
                if await migrate_record_vector(record_id, raw):
                    totals["record_embeddings"] += 1

    logger.info(
        "Migration complete: "
        f"{totals['records']} records, {totals['relations']} relations, "
        f"{totals['source_embeddings']} source embeddings, "
        f"{totals['record_embeddings']} note/insight embeddings, "
        f"{totals['skipped']} operational/invalid rows skipped"
    )


async def main_async(args: argparse.Namespace) -> None:
    reader = SurrealReader(
        args.surreal_url,
        args.surreal_user,
        args.surreal_password,
        args.surreal_namespace,
        args.surreal_database,
    )
    await migrate(reader, allow_nonempty=args.allow_nonempty)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate Open Notebook SurrealDB data to PostgreSQL"
    )
    parser.add_argument(
        "--surreal-url", default=os.getenv("SURREAL_URL", "http://localhost:8000")
    )
    parser.add_argument("--surreal-user", default=os.getenv("SURREAL_USER", "root"))
    parser.add_argument(
        "--surreal-password",
        default=os.getenv("SURREAL_PASSWORD") or os.getenv("SURREAL_PASS") or "root",
    )
    parser.add_argument(
        "--surreal-namespace", default=os.getenv("SURREAL_NAMESPACE", "open_notebook")
    )
    parser.add_argument(
        "--surreal-database", default=os.getenv("SURREAL_DATABASE", "open_notebook")
    )
    parser.add_argument("--allow-nonempty", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
