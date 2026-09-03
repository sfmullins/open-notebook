"""PostgreSQL-native embedding storage and knowledge-base search."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from open_notebook.database.postgres import db_connection, ensure_schema
from open_notebook.database.record_id import RecordID


async def ensure_embedding_schema() -> None:
    await ensure_schema()
    async with db_connection() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS record_embedding_pg (
                table_name text NOT NULL,
                record_key text NOT NULL,
                content text NOT NULL,
                embedding vector NOT NULL,
                updated timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(table_name, record_key)
            )
            """
        )
        await connection.execute(
            "CREATE INDEX IF NOT EXISTS record_embedding_pg_table_idx ON record_embedding_pg(table_name, record_key)"
        )
        await connection.commit()


def vector_literal(embedding: Sequence[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in embedding) + "]"


async def upsert_record_embedding(
    record_id: RecordID, content: str, embedding: Sequence[float]
) -> None:
    await ensure_embedding_schema()
    async with db_connection() as connection:
        await connection.execute(
            """
            INSERT INTO record_embedding_pg(table_name, record_key, content, embedding)
            VALUES (%s,%s,%s,%s::vector)
            ON CONFLICT(table_name, record_key) DO UPDATE
            SET content=EXCLUDED.content, embedding=EXCLUDED.embedding, updated=now()
            """,
            (record_id.table, record_id.id, content, vector_literal(embedding)),
        )
        await connection.commit()


async def delete_source_embeddings(source_id: RecordID) -> None:
    await ensure_embedding_schema()
    async with db_connection() as connection:
        await connection.execute(
            "DELETE FROM source_embedding_pg WHERE source_key=%s", (source_id.id,)
        )
        await connection.commit()


async def replace_source_embeddings(
    source_id: RecordID, records: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    await ensure_embedding_schema()
    created: list[dict[str, Any]] = []
    async with db_connection() as connection:
        await connection.execute(
            "DELETE FROM source_embedding_pg WHERE source_key=%s", (source_id.id,)
        )
        for record in records:
            order_index = int(record.get("order") or 0)
            content = str(record.get("content") or "")
            embedding = record.get("embedding") or []
            row = await (
                await connection.execute(
                    """
                    INSERT INTO source_embedding_pg(source_key, order_index, content, embedding)
                    VALUES (%s,%s,%s,%s::vector)
                    RETURNING id, source_key, order_index, content
                    """,
                    (source_id.id, order_index, content, vector_literal(embedding)),
                )
            ).fetchone()
            if row:
                created.append(
                    {
                        "id": f"source_embedding:{row['id']}",
                        "source": str(source_id),
                        "order": row["order_index"],
                        "content": row["content"],
                        "embedding": list(embedding),
                    }
                )
        await connection.commit()
    return created


async def count_source_embeddings(source_id: RecordID) -> int:
    await ensure_embedding_schema()
    async with db_connection() as connection:
        row = await (
            await connection.execute(
                "SELECT count(*) AS n FROM source_embedding_pg WHERE source_key=%s",
                (source_id.id,),
            )
        ).fetchone()
    return int(row["n"]) if row else 0


async def source_ids_with_embeddings() -> list[str]:
    await ensure_embedding_schema()
    async with db_connection() as connection:
        rows = await (await connection.execute(
            "SELECT DISTINCT source_key FROM source_embedding_pg ORDER BY source_key"
        )).fetchall()
    return [f"source:{row['source_key']}" for row in rows]


async def count_distinct_source_embeddings() -> int:
    await ensure_embedding_schema()
    async with db_connection() as connection:
        row = await (await connection.execute(
            "SELECT count(DISTINCT source_key) AS n FROM source_embedding_pg"
        )).fetchone()
    return int(row['n']) if row else 0


async def record_ids_with_embeddings(table: str) -> list[str]:
    await ensure_embedding_schema()
    async with db_connection() as connection:
        rows = await (await connection.execute(
            "SELECT record_key FROM record_embedding_pg WHERE table_name=%s ORDER BY record_key",
            (table,),
        )).fetchall()
    return [f"{table}:{row['record_key']}" for row in rows]


async def count_record_embeddings(table: str) -> int:
    await ensure_embedding_schema()
    async with db_connection() as connection:
        row = await (await connection.execute(
            "SELECT count(*) AS n FROM record_embedding_pg WHERE table_name=%s",
            (table,),
        )).fetchone()
    return int(row['n']) if row else 0


async def text_search_pg(
    keyword: str, results: int, source: bool = True, note: bool = True
) -> list[dict[str, Any]]:
    """Full-text search preserving the legacy response fields.

    PostgreSQL's built-in text-search ranking replaces SurrealDB BM25.  Ranking
    numbers are therefore not numerically identical, but ordering and response
    shape remain stable.
    """
    await ensure_embedding_schema()
    clauses: list[str] = []
    params: list[Any] = []

    if source:
        clauses.append(
            """
            SELECT 'source:' || r.record_key AS id,
                   'source:' || r.record_key AS parent_id,
                   COALESCE(r.data->>'title','') AS title,
                   ts_rank(
                       to_tsvector('simple', COALESCE(r.data->>'title','') || ' ' || COALESCE(r.data->>'full_text','')),
                       websearch_to_tsquery('simple', %s)
                   ) AS relevance
            FROM on_record r
            WHERE r.table_name='source'
              AND to_tsvector('simple', COALESCE(r.data->>'title','') || ' ' || COALESCE(r.data->>'full_text',''))
                  @@ websearch_to_tsquery('simple', %s)
            """
        )
        params.extend([keyword, keyword])
        clauses.append(
            """
            SELECT 'source:' || e.source_key AS id,
                   'source:' || e.source_key AS parent_id,
                   COALESCE(s.data->>'title','') AS title,
                   ts_rank(to_tsvector('simple', e.content), websearch_to_tsquery('simple', %s)) AS relevance
            FROM source_embedding_pg e
            LEFT JOIN on_record s ON s.table_name='source' AND s.record_key=e.source_key
            WHERE to_tsvector('simple', e.content) @@ websearch_to_tsquery('simple', %s)
            """
        )
        params.extend([keyword, keyword])
        clauses.append(
            """
            SELECT 'source_insight:' || i.record_key AS id,
                   COALESCE(i.data->>'source','') AS parent_id,
                   COALESCE(i.data->>'insight_type','Insight') || ' - ' || COALESCE(s.data->>'title','') AS title,
                   ts_rank(to_tsvector('simple', COALESCE(i.data->>'content','')), websearch_to_tsquery('simple', %s)) AS relevance
            FROM on_record i
            LEFT JOIN on_record s
              ON s.table_name='source' AND ('source:' || s.record_key)=i.data->>'source'
            WHERE i.table_name='source_insight'
              AND to_tsvector('simple', COALESCE(i.data->>'content','')) @@ websearch_to_tsquery('simple', %s)
            """
        )
        params.extend([keyword, keyword])

    if note:
        clauses.append(
            """
            SELECT 'note:' || n.record_key AS id,
                   'note:' || n.record_key AS parent_id,
                   COALESCE(n.data->>'title','') AS title,
                   ts_rank(
                       to_tsvector('simple', COALESCE(n.data->>'title','') || ' ' || COALESCE(n.data->>'content','')),
                       websearch_to_tsquery('simple', %s)
                   ) AS relevance
            FROM on_record n
            WHERE n.table_name='note'
              AND to_tsvector('simple', COALESCE(n.data->>'title','') || ' ' || COALESCE(n.data->>'content',''))
                  @@ websearch_to_tsquery('simple', %s)
            """
        )
        params.extend([keyword, keyword])

    if not clauses:
        return []

    union_sql = " UNION ALL ".join(clauses)
    query = f"""
        SELECT id, parent_id, title, max(relevance) AS relevance
        FROM ({union_sql}) candidates
        GROUP BY id, parent_id, title
        ORDER BY relevance DESC
        LIMIT %s
    """
    params.append(results)
    async with db_connection() as connection:
        rows = await (await connection.execute(query, tuple(params))).fetchall()
    return [dict(row) for row in rows]


async def vector_search_pg(
    embedding: Sequence[float],
    results: int,
    source: bool = True,
    note: bool = True,
    minimum_score: float = 0.2,
) -> list[dict[str, Any]]:
    await ensure_embedding_schema()
    vector = vector_literal(embedding)
    clauses: list[str] = []
    params: list[Any] = []

    if source:
        clauses.append(
            """
            SELECT 'source:' || e.source_key AS id,
                   'source:' || e.source_key AS parent_id,
                   COALESCE(s.data->>'title','') AS title,
                   e.content AS match,
                   1 - (e.embedding <=> %s::vector) AS similarity
            FROM source_embedding_pg e
            LEFT JOIN on_record s ON s.table_name='source' AND s.record_key=e.source_key
            WHERE 1 - (e.embedding <=> %s::vector) >= %s
            """
        )
        params.extend([vector, vector, minimum_score])
        clauses.append(
            """
            SELECT 'source_insight:' || e.record_key AS id,
                   COALESCE(i.data->>'source','') AS parent_id,
                   COALESCE(i.data->>'insight_type','Insight') || ' - ' || COALESCE(s.data->>'title','') AS title,
                   e.content AS match,
                   1 - (e.embedding <=> %s::vector) AS similarity
            FROM record_embedding_pg e
            JOIN on_record i ON i.table_name='source_insight' AND i.record_key=e.record_key
            LEFT JOIN on_record s ON s.table_name='source' AND ('source:' || s.record_key)=i.data->>'source'
            WHERE e.table_name='source_insight'
              AND 1 - (e.embedding <=> %s::vector) >= %s
            """
        )
        params.extend([vector, vector, minimum_score])

    if note:
        clauses.append(
            """
            SELECT 'note:' || e.record_key AS id,
                   'note:' || e.record_key AS parent_id,
                   COALESCE(n.data->>'title','') AS title,
                   e.content AS match,
                   1 - (e.embedding <=> %s::vector) AS similarity
            FROM record_embedding_pg e
            JOIN on_record n ON n.table_name='note' AND n.record_key=e.record_key
            WHERE e.table_name='note'
              AND 1 - (e.embedding <=> %s::vector) >= %s
            """
        )
        params.extend([vector, vector, minimum_score])

    if not clauses:
        return []

    union_sql = " UNION ALL ".join(clauses)
    query = f"""
        SELECT id, parent_id, title, max(similarity) AS similarity,
               array_agg(match ORDER BY similarity DESC) AS matches
        FROM ({union_sql}) candidates
        GROUP BY id, parent_id, title
        ORDER BY similarity DESC
        LIMIT %s
    """
    params.append(results)
    async with db_connection() as connection:
        rows = await (await connection.execute(query, tuple(params))).fetchall()
    return [dict(row) for row in rows]
