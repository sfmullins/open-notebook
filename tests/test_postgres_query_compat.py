"""Integration tests for high-value legacy query contracts on PostgreSQL."""

from __future__ import annotations

import pytest

from open_notebook.database.embeddings import replace_source_embeddings
from open_notebook.database.postgres import db_connection, ensure_schema
from open_notebook.database.record_id import RecordID
from open_notebook.database.repository import repo_query, repo_relate, repo_upsert


async def _reset_store() -> None:
    await ensure_schema()
    async with db_connection() as connection:
        await connection.execute(
            "TRUNCATE TABLE on_relation, on_record, source_embedding_pg, "
            "record_embedding_pg, command_job CASCADE"
        )
        await connection.commit()


@pytest.mark.asyncio
async def test_source_list_adapter_preserves_counts_embedding_and_notebook_filter() -> None:
    await _reset_store()
    await repo_upsert("notebook", "notebook:n1", {"name": "N1", "description": ""})
    await repo_upsert(
        "source",
        "source:s1",
        {"title": "Alpha", "topics": [], "full_text": "body", "asset": None},
    )
    await repo_upsert(
        "source",
        "source:s2",
        {"title": "Beta", "topics": [], "full_text": "other", "asset": None},
    )
    await repo_relate("source:s1", "reference", "notebook:n1")
    await repo_upsert(
        "source_insight",
        "source_insight:i1",
        {"source": "source:s1", "insight_type": "summary", "content": "summary"},
    )
    await replace_source_embeddings(
        RecordID.parse("source:s1"),
        [
            {
                "source": "source:s1",
                "order": 0,
                "content": "body",
                "embedding": [1.0, 0.0],
            }
        ],
    )

    result = await repo_query(
        """
        SELECT id, asset, created, title, updated, topics, command,
               string::lowercase(title OR '') AS title_sort,
               ('text') AS type,
               (SELECT VALUE count() FROM source_insight WHERE source = $parent.id GROUP ALL)[0].count OR 0 AS insights_count,
               (SELECT VALUE id FROM source_embedding WHERE source = $parent.id LIMIT 1) != [] AS embedded
        FROM (select value in from reference where out=$notebook_id)
        ORDER BY title_sort ASC, id ASC
        LIMIT $limit START $offset
        FETCH command
        """,
        {
            "notebook_id": RecordID.parse("notebook:n1"),
            "limit": 50,
            "offset": 0,
        },
    )

    assert [row["id"] for row in result] == ["source:s1"]
    assert result[0]["insights_count"] == 1
    assert result[0]["embedded"] is True
    assert result[0]["title_sort"] == "alpha"


@pytest.mark.asyncio
async def test_notebook_relation_fetches_preserve_legacy_shapes() -> None:
    await _reset_store()
    await repo_upsert("notebook", "notebook:n1", {"name": "N1", "description": ""})
    await repo_upsert(
        "source", "source:s1", {"title": "Source", "full_text": "secret body"}
    )
    await repo_upsert(
        "note", "note:x1", {"title": "Note", "content": "note body"}
    )
    await repo_relate("source:s1", "reference", "notebook:n1")
    await repo_relate("note:x1", "artifact", "notebook:n1")

    sources = await repo_query(
        """
        select * omit source.full_text from (
            select in as source from reference where out=$id
            fetch source
        ) order by source.updated desc
        """,
        {"id": RecordID.parse("notebook:n1")},
    )
    notes = await repo_query(
        """
        select * omit note.content, note.embedding from (
            select in as note from artifact where out=$id
            fetch note
        ) order by note.updated desc
        """,
        {"id": RecordID.parse("notebook:n1")},
    )

    assert sources[0]["source"]["id"] == "source:s1"
    assert "full_text" not in sources[0]["source"]
    assert notes[0]["note"]["id"] == "note:x1"
    assert "content" not in notes[0]["note"]


@pytest.mark.asyncio
async def test_notebook_count_projection_uses_relations() -> None:
    await _reset_store()
    await repo_upsert("notebook", "notebook:n1", {"name": "N1", "description": ""})
    await repo_upsert("source", "source:s1", {"title": "Source"})
    await repo_upsert("note", "note:x1", {"title": "Note", "content": "body"})
    await repo_relate("source:s1", "reference", "notebook:n1")
    await repo_relate("note:x1", "artifact", "notebook:n1")

    rows = await repo_query(
        """
        SELECT *,
               count(<-reference.in) as source_count,
               count(<-artifact.in) as note_count
        FROM notebook
        ORDER BY name asc
        """
    )

    assert rows[0]["source_count"] == 1
    assert rows[0]["note_count"] == 1
