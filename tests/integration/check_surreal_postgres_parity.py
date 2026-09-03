#!/usr/bin/env python3
"""Validate semantic parity after the one-time legacy-store migration."""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from open_notebook.database.postgres import db_connection, ensure_schema
from scripts.migrate_surreal_to_postgres import postgres_is_empty


def parse_vector(value: object) -> list[float]:
    text = str(value).strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise AssertionError(f"Unexpected pgvector representation: {value!r}")
    if text == "[]":
        return []
    return [float(item) for item in text[1:-1].split(",")]


def assert_vector(actual: object, expected: list[float]) -> None:
    values = parse_vector(actual)
    assert len(values) == len(expected), (values, expected)
    for got, want in zip(values, expected, strict=True):
        assert math.isclose(got, want, rel_tol=1e-6, abs_tol=1e-6), (values, expected)


async def main() -> None:
    await ensure_schema()
    async with db_connection() as connection:
        notebook = await (
            await connection.execute(
                """
                SELECT data
                FROM on_record
                WHERE table_name='notebook' AND record_key='parity'
                """
            )
        ).fetchone()
        assert notebook is not None
        assert notebook["data"]["name"] == "Parity Notebook"
        assert notebook["data"]["description"] == "migration fixture"

        source = await (
            await connection.execute(
                """
                SELECT data
                FROM on_record
                WHERE table_name='source' AND record_key='parity'
                """
            )
        ).fetchone()
        assert source is not None
        assert source["data"]["title"] == "Parity Source"
        assert source["data"]["full_text"] == "Fixture body"

        note = await (
            await connection.execute(
                """
                SELECT data
                FROM on_record
                WHERE table_name='note' AND record_key='parity'
                """
            )
        ).fetchone()
        assert note is not None
        assert note["data"]["title"] == "Parity Note"
        assert note["data"]["content"] == "note body"

        relation = await (
            await connection.execute(
                """
                SELECT kind, source_table, source_key, target_table, target_key, data
                FROM on_relation
                WHERE kind='reference'
                  AND source_table='source' AND source_key='parity'
                  AND target_table='notebook' AND target_key='parity'
                """
            )
        ).fetchone()
        assert relation is not None
        assert relation["data"]["role"] == "primary"

        source_embedding = await (
            await connection.execute(
                """
                SELECT source_key, order_index, content, embedding::text AS embedding
                FROM source_embedding_pg
                WHERE source_key='parity' AND order_index=0
                """
            )
        ).fetchone()
        assert source_embedding is not None
        assert source_embedding["content"] == "fixture chunk"
        assert_vector(source_embedding["embedding"], [0.1, 0.2, 0.3])

        record_embedding = await (
            await connection.execute(
                """
                SELECT table_name, record_key, content, embedding::text AS embedding
                FROM record_embedding_pg
                WHERE table_name='note' AND record_key='parity'
                """
            )
        ).fetchone()
        assert record_embedding is not None
        assert record_embedding["content"] == "note body"
        assert_vector(record_embedding["embedding"], [0.4, 0.5, 0.6])

        # Prove the migration target guard treats record-only vectors as data.
        await connection.execute("DELETE FROM on_relation")
        await connection.execute("DELETE FROM source_embedding_pg")
        await connection.execute("DELETE FROM on_record")
        await connection.commit()

    assert not await postgres_is_empty(), (
        "record_embedding_pg rows must make the migration target non-empty"
    )

    async with db_connection() as connection:
        await connection.execute("DELETE FROM record_embedding_pg")
        await connection.commit()

    assert await postgres_is_empty(), "all migration target tables are now empty"
    print("SurrealDB 2.6.5 -> PostgreSQL semantic migration parity verified.")


if __name__ == "__main__":
    asyncio.run(main())
