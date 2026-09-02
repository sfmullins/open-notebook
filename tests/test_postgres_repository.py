"""PostgreSQL repository contract tests."""

from __future__ import annotations

import pytest

from open_notebook.database.postgres import db_connection, ensure_schema
from open_notebook.database.record_id import RecordID
from open_notebook.database.repository import repo_query, repo_relate, repo_upsert


async def _reset_store() -> None:
    await ensure_schema()
    async with db_connection() as connection:
        await connection.execute("TRUNCATE TABLE on_relation, on_record CASCADE")
        await connection.commit()


def test_record_id_round_trip() -> None:
    record_id = RecordID.parse("source:abc-123")
    assert record_id.table == "source"
    assert record_id.id == "abc-123"
    assert str(record_id) == "source:abc-123"


@pytest.mark.asyncio
async def test_singleton_upsert_preserves_explicit_record_id() -> None:
    await _reset_store()

    result = await repo_upsert(
        "record",
        "open_notebook:default_models",
        {"default_chat_model": "model:chat"},
    )

    assert result[0]["id"] == "open_notebook:default_models"
    loaded = await repo_query(
        "SELECT * FROM ONLY $record_id",
        {"record_id": RecordID.parse("open_notebook:default_models")},
    )
    assert loaded[0]["default_chat_model"] == "model:chat"


@pytest.mark.asyncio
async def test_relation_direction_matches_legacy_in_out_contract() -> None:
    await _reset_store()
    await repo_upsert("source", "source:s1", {"title": "Source"})
    await repo_upsert("notebook", "notebook:n1", {"name": "Notebook"})

    first = await repo_relate("source:s1", "reference", "notebook:n1")
    second = await repo_relate("source:s1", "reference", "notebook:n1")

    # Upsert semantics make linking idempotent.
    assert first[0]["in"] == "source:s1"
    assert first[0]["out"] == "notebook:n1"
    assert second[0]["in"] == "source:s1"
    assert second[0]["out"] == "notebook:n1"

    rows = await repo_query(
        "SELECT * FROM reference WHERE in = $source AND out = $notebook",
        {
            "source": RecordID.parse("source:s1"),
            "notebook": RecordID.parse("notebook:n1"),
        },
    )
    assert len(rows) == 1
