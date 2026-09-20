"""Question-scoped retrieval primitives for notebook chat.

This stays in the database layer so notebook-chat context assembly does not
embed PostgreSQL/pgvector SQL in a router or utility module.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from open_notebook.database.embeddings import vector_literal
from open_notebook.database.postgres import db_connection, ensure_schema
from open_notebook.database.record_id import RecordID


def _source_key(source_id: str | RecordID) -> str:
    text = str(source_id)
    return text.split(":", 1)[1] if text.startswith("source:") else text


async def retrieve_source_chunks_pg(
    embedding: Sequence[float],
    source_ids: Sequence[str | RecordID],
    *,
    limit: int = 32,
    minimum_score: float = 0.2,
) -> list[dict[str, Any]]:
    """Return the most relevant embedded chunks from selected sources only."""
    keys = sorted({_source_key(source_id) for source_id in source_ids if source_id})
    if not keys or limit <= 0:
        return []

    await ensure_schema()
    vector = vector_literal(embedding)
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                """
                SELECT e.id,
                       e.source_key,
                       e.order_index,
                       e.content,
                       1 - (e.embedding <=> %s::vector) AS similarity
                FROM source_embedding_pg e
                WHERE e.source_key = ANY(%s)
                  AND 1 - (e.embedding <=> %s::vector) >= %s
                ORDER BY similarity DESC, e.source_key, e.order_index
                LIMIT %s
                """,
                (vector, keys, vector, minimum_score, limit),
            )
        ).fetchall()

    return [
        {
            "id": f"source_embedding:{row['id']}",
            "source_id": f"source:{row['source_key']}",
            "order": int(row["order_index"]),
            "content": str(row["content"]),
            "similarity": float(row["similarity"]),
        }
        for row in rows
    ]
