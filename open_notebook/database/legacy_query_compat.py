"""Native PostgreSQL adapters for complex legacy repository queries.

The public repository API is temporarily stable while high-value call sites still
emit SurrealQL-shaped strings. This module recognizes those exact contracts and
executes the equivalent PostgreSQL operations. Unknown queries return
``NOT_HANDLED`` and fall through to the simpler compatibility parser.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from open_notebook.database.postgres import db_connection, ensure_schema, get_record, list_records
from open_notebook.database.record_id import RecordID

NOT_HANDLED = object()


def _normalize(query: str) -> str:
    return " ".join(query.strip().rstrip(";").split())


def _rid(value: Any) -> RecordID:
    return RecordID.parse(str(value))


async def _relations(kind: str) -> list[dict[str, Any]]:
    await ensure_schema()
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                """
                SELECT id, source_table, source_key, target_table, target_key, data
                FROM on_relation WHERE kind=%s
                """,
                (kind,),
            )
        ).fetchall()
    return [
        {
            "id": f"{kind}:{row['id']}",
            "in": f"{row['source_table']}:{row['source_key']}",
            "out": f"{row['target_table']}:{row['target_key']}",
            **dict(row.get("data") or {}),
        }
        for row in rows
    ]


async def _linked_records(
    kind: str,
    target: RecordID,
    source_table: str,
    *,
    omit: set[str] | None = None,
) -> list[dict[str, Any]]:
    relations = await _relations(kind)
    source_ids = [row["in"] for row in relations if row["out"] == str(target)]
    records: list[dict[str, Any]] = []
    for source_id in source_ids:
        rid = _rid(source_id)
        if rid.table != source_table:
            continue
        record = await get_record(rid)
        if record:
            for field in omit or set():
                record.pop(field, None)
            records.append(record)
    records.sort(key=lambda row: str(row.get("updated") or ""), reverse=True)
    return records


async def _notebook_counts(notebook_id: str) -> tuple[int, int]:
    reference = await _relations("reference")
    artifact = await _relations("artifact")
    return (
        sum(1 for row in reference if row["out"] == notebook_id),
        sum(1 for row in artifact if row["out"] == notebook_id),
    )


async def _notebook_list(query: str) -> list[dict[str, Any]]:
    rows = await list_records("notebook")
    enriched: list[dict[str, Any]] = []
    for row in rows:
        source_count, note_count = await _notebook_counts(str(row["id"]))
        enriched.append({**row, "source_count": source_count, "note_count": note_count})

    order = re.search(r"ORDER BY ([a-z_]+)(?: (ASC|DESC))?", query, re.IGNORECASE)
    if order:
        field = order.group(1)
        reverse = (order.group(2) or "ASC").upper() == "DESC"
        enriched.sort(
            key=lambda row: (row.get(field) is None, str(row.get(field) or "")),
            reverse=reverse,
        )
    return enriched


async def _notebook_single(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    rid = _rid(params["notebook_id"])
    row = await get_record(rid)
    if not row:
        return []
    source_count, note_count = await _notebook_counts(str(rid))
    return [{**row, "source_count": source_count, "note_count": note_count}]


async def _source_listing(query: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    sources = await list_records("source")

    notebook_id = params.get("notebook_id")
    if notebook_id is not None:
        notebook = str(_rid(notebook_id))
        references = await _relations("reference")
        allowed = {row["in"] for row in references if row["out"] == notebook}
        sources = [row for row in sources if str(row["id"]) in allowed]

    insights = await list_records("source_insight")
    insight_counts: dict[str, int] = {}
    for insight in insights:
        source_id = str(insight.get("source") or "")
        insight_counts[source_id] = insight_counts.get(source_id, 0) + 1

    await ensure_schema()
    async with db_connection() as connection:
        embedded_rows = await (
            await connection.execute("SELECT DISTINCT source_key FROM source_embedding_pg")
        ).fetchall()
    embedded = {f"source:{row['source_key']}" for row in embedded_rows}

    command_ids = [str(row["command"]) for row in sources if row.get("command")]
    command_map: dict[str, Any] = {}
    if command_ids:
        from surreal_commands import get_command_statuses

        command_map = await get_command_statuses(command_ids)

    result: list[dict[str, Any]] = []
    for row in sources:
        source_id = str(row["id"])
        asset = row.get("asset") or {}
        if isinstance(asset, Mapping) and asset.get("file_path") is not None:
            source_type = "file"
        elif isinstance(asset, Mapping) and asset.get("url") is not None:
            source_type = "link"
        else:
            source_type = "text"

        command = row.get("command")
        command_status = command_map.get(str(command)) if command else None
        fetched_command = (
            command_status.model_dump(mode="json") if command_status is not None else command
        )
        result.append(
            {
                **row,
                "command": fetched_command,
                "title_sort": str(row.get("title") or "").lower(),
                "type": source_type,
                "insights_count": insight_counts.get(source_id, 0),
                "embedded": source_id in embedded,
            }
        )

    order = re.search(
        r"ORDER BY ([a-z_]+) (ASC|DESC), id ASC", query, re.IGNORECASE
    )
    if order:
        field = order.group(1)
        reverse = order.group(2).upper() == "DESC"
        result.sort(
            key=lambda row: (row.get(field) is None, row.get(field), str(row.get("id"))),
            reverse=reverse,
        )

    offset = int(params.get("offset", 0))
    limit = int(params.get("limit", 50))
    return result[offset : offset + limit]


async def _source_link_counts(notebook_id: RecordID) -> list[dict[str, Any]]:
    references = await _relations("reference")
    source_ids = [row["in"] for row in references if row["out"] == str(notebook_id)]
    result = []
    for source_id in source_ids:
        assigned_others = sum(
            1
            for row in references
            if row["in"] == source_id and row["out"] != str(notebook_id)
        )
        result.append({"id": source_id, "assigned_others": assigned_others})
    return result


async def _embedding_source(query: str, params: Mapping[str, Any]) -> Any:
    rid = _rid(params["id"])
    source_id: str | None = None
    if rid.table == "source_insight":
        insight = await get_record(rid)
        source_id = str(insight.get("source")) if insight and insight.get("source") else None
    elif rid.table == "source_embedding":
        await ensure_schema()
        async with db_connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT source_key FROM source_embedding_pg WHERE id=%s",
                    (rid.id,),
                )
            ).fetchone()
        source_id = f"source:{row['source_key']}" if row else None
    if not source_id:
        return []
    source = await get_record(_rid(source_id))
    return [{"source": source}] if source else []


async def try_query(query_str: str, vars: Mapping[str, Any] | None = None) -> Any:
    params = vars or {}
    query = _normalize(query_str)
    upper = query.upper()

    # Main source list endpoint: projections/subqueries/FETCH are evaluated here.
    if (
        upper.startswith("SELECT ID, ASSET, CREATED, TITLE, UPDATED, TOPICS, COMMAND")
        and "INSIGHTS_COUNT" in upper
        and " AS EMBEDDED" in upper
    ):
        return await _source_listing(query, params)

    # Notebook list/single responses with relation counts.
    if "COUNT(<-REFERENCE.IN) AS SOURCE_COUNT" in upper and "COUNT(<-ARTIFACT.IN) AS NOTE_COUNT" in upper:
        if "FROM $NOTEBOOK_ID" in upper:
            return await _notebook_single(params)
        if "FROM NOTEBOOK" in upper:
            return await _notebook_list(query)

    # Notebook.get_sources(): nested relation fetch.
    if "SELECT IN AS SOURCE FROM REFERENCE WHERE OUT=$ID" in upper:
        omit = {"full_text"} if "OMIT SOURCE.FULL_TEXT" in upper else set()
        rows = await _linked_records("reference", _rid(params["id"]), "source", omit=omit)
        return [{"source": row} for row in rows]

    # Notebook.get_notes(): nested relation fetch.
    if "SELECT IN AS NOTE FROM ARTIFACT WHERE OUT=$ID" in upper:
        omit: set[str] = {"embedding"}
        if "OMIT NOTE.CONTENT" in upper:
            omit.add("content")
        rows = await _linked_records("artifact", _rid(params["id"]), "note", omit=omit)
        return [{"note": row} for row in rows]

    # Notebook.get_chat_sessions(). Legacy shape wraps the fetched record in a list.
    if "FROM REFERS_TO" in upper and "WHERE OUT=$ID" in upper and "CHAT_SESSION" in upper:
        rows = await _linked_records(
            "refers_to", _rid(params["id"]), "chat_session"
        )
        return [{"chat_session": [row]} for row in rows]

    # Notebook delete preview / exclusive-source detection.
    if "ASSIGNED_OTHERS" in upper and "<-REFERENCE.IN AS SOURCES" in upper:
        return await _source_link_counts(_rid(params["notebook_id"]))

    # Relationship counts used during notebook deletion.
    count_relation = re.fullmatch(
        r"SELECT count\(\) as count FROM (reference|artifact) WHERE out = \$(\w+) GROUP ALL",
        query,
        re.IGNORECASE,
    )
    if count_relation:
        kind, param_name = count_relation.groups()
        target = str(_rid(params[param_name]))
        return [{"count": sum(1 for row in await _relations(kind) if row["out"] == target)}]

    # Source embedding count.
    if "SELECT COUNT() AS CHUNKS FROM SOURCE_EMBEDDING WHERE SOURCE=$ID GROUP ALL" in upper:
        source = _rid(params["id"])
        await ensure_schema()
        async with db_connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT count(*) AS chunks FROM source_embedding_pg WHERE source_key=%s",
                    (source.id,),
                )
            ).fetchone()
        return [{"chunks": int(row["chunks"])}] if row else []

    # Source/insight embedding back-reference lookup.
    if upper.startswith("SELECT SOURCE.* FROM $ID FETCH SOURCE"):
        return await _embedding_source(query, params)

    # Generic record cascade delete by a foreign record-id field (currently
    # source_insight WHERE source=$source_id).
    delete_records = re.fullmatch(
        r"DELETE (\w+) WHERE (\w+) = \$(\w+)", query, re.IGNORECASE
    )
    if delete_records and delete_records.group(1) != "source_embedding":
        table, field, param_name = delete_records.groups()
        expected = str(_rid(params[param_name]))
        rows = await list_records(table)
        matches = [row for row in rows if str(row.get(field) or "") == expected]
        async with db_connection() as connection:
            for row in matches:
                rid = _rid(row["id"])
                await connection.execute(
                    "DELETE FROM on_record WHERE table_name=%s AND record_key=%s",
                    (rid.table, rid.id),
                )
                # record_embedding_pg may not exist on a brand-new database,
                # so only remove from it when present.
                await connection.execute(
                    """
                    DELETE FROM record_embedding_pg
                    WHERE table_name=%s AND record_key=%s
                    """,
                    (rid.table, rid.id),
                )
            await connection.commit()
        return matches

    return NOT_HANDLED
