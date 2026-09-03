"""Native PostgreSQL repository operations.

The application keeps stable ``table:key`` record identifiers while all runtime
persistence is PostgreSQL-backed.  This module deliberately exposes structured
operations rather than accepting database query-language strings: historical
SurrealDB syntax belongs only in the one-time migration tooling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Union

from loguru import logger

from open_notebook.database.postgres import (
    create_record,
    db_connection,
    delete_record,
    ensure_schema,
    get_database_url,
    get_record,
    list_records,
    normalize_json,
    relate,
    update_record,
)
from open_notebook.database.record_id import RecordID


def _value(value: Any) -> Any:
    return str(value) if isinstance(value, RecordID) else value


def _row_field(row: Mapping[str, Any], field: str) -> Any:
    current: Any = row
    for part in field.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def get_database_password() -> str:
    """Compatibility accessor; PostgreSQL credentials are carried by DATABASE_URL."""
    return ""


def get_database_namespace() -> str:
    return "open_notebook"


def get_database_name() -> str:
    return get_database_url().rsplit("/", 1)[-1].split("?", 1)[0]


def parse_record_ids(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: parse_record_ids(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [parse_record_ids(value) for value in obj]
    if isinstance(obj, RecordID):
        return str(obj)
    return obj


def ensure_record_id(value: Union[str, RecordID]) -> RecordID:
    return RecordID.parse(value)


async def repo_healthcheck() -> bool:
    await ensure_schema()
    async with db_connection() as connection:
        row = await (await connection.execute("SELECT 1 AS ok")).fetchone()
    return bool(row and row.get("ok") == 1)


async def repo_get(record_id: Union[str, RecordID]) -> Optional[Dict[str, Any]]:
    return await get_record(ensure_record_id(record_id))


async def repo_list(
    table: str,
    *,
    filters: Optional[Mapping[str, Any]] = None,
    exclude: Optional[Mapping[str, Any]] = None,
    in_filters: Optional[Mapping[str, Iterable[Any]]] = None,
    case_insensitive_fields: Iterable[str] = (),
    non_null_fields: Iterable[str] = (),
    non_empty_fields: Iterable[str] = (),
    array_non_empty_fields: Iterable[str] = (),
    order_by: Optional[str] = None,
    descending: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[Dict[str, Any]]:
    """List records using structured predicates, never query-language strings."""
    rows = await list_records(table)
    ci = set(case_insensitive_fields)

    def matches(row: Mapping[str, Any]) -> bool:
        for field, expected in (filters or {}).items():
            actual = _row_field(row, field)
            if field in ci:
                if str(_value(actual) or "").lower() != str(_value(expected) or "").lower():
                    return False
            elif _value(actual) != _value(expected):
                return False
        for field, forbidden in (exclude or {}).items():
            if _value(_row_field(row, field)) == _value(forbidden):
                return False
        for field, allowed in (in_filters or {}).items():
            allowed_values = {_value(value) for value in allowed}
            if _value(_row_field(row, field)) not in allowed_values:
                return False
        for field in non_null_fields:
            if _row_field(row, field) is None:
                return False
        for field in non_empty_fields:
            if not str(_row_field(row, field) or "").strip():
                return False
        for field in array_non_empty_fields:
            value = _row_field(row, field)
            if not isinstance(value, list) or not value:
                return False
        return True

    rows = [row for row in rows if matches(row)]
    if order_by:
        rows.sort(
            key=lambda row: (
                _row_field(row, order_by) is None,
                _row_field(row, order_by),
            ),
            reverse=descending,
        )
    start = max(0, offset)
    stop = None if limit is None else start + max(0, limit)
    return rows[start:stop]


async def repo_count(table: str, **list_kwargs: Any) -> int:
    return len(await repo_list(table, **list_kwargs))


async def repo_exists(table: str, **list_kwargs: Any) -> bool:
    list_kwargs["limit"] = 1
    return bool(await repo_list(table, **list_kwargs))


async def repo_create(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(data)
    payload.pop("id", None)
    return await create_record(table, payload)


async def repo_relate(
    source: Union[str, RecordID],
    relationship: str,
    target: Union[str, RecordID],
    data: Optional[Dict[str, Any]] = None,
) -> list[Dict[str, Any]]:
    row = await relate(
        ensure_record_id(source),
        relationship,
        ensure_record_id(target),
        data or {},
    )
    return [row]


async def repo_relations(
    relationship: str,
    *,
    source: Optional[Union[str, RecordID]] = None,
    target: Optional[Union[str, RecordID]] = None,
) -> list[Dict[str, Any]]:
    """Return relation rows constrained by kind/source/target."""
    await ensure_schema()
    clauses = ["kind=%s"]
    params: list[Any] = [relationship]
    if source is not None:
        source_id = ensure_record_id(source)
        clauses.extend(["source_table=%s", "source_key=%s"])
        params.extend([source_id.table, source_id.id])
    if target is not None:
        target_id = ensure_record_id(target)
        clauses.extend(["target_table=%s", "target_key=%s"])
        params.extend([target_id.table, target_id.id])
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                "SELECT id, kind, source_table, source_key, target_table, target_key, data, created "
                "FROM on_relation WHERE " + " AND ".join(clauses),
                tuple(params),
            )
        ).fetchall()
    return [
        {
            "id": f"{row['kind']}:{row['id']}",
            "in": f"{row['source_table']}:{row['source_key']}",
            "out": f"{row['target_table']}:{row['target_key']}",
            **dict(row.get("data") or {}),
        }
        for row in rows
    ]


async def repo_relation_count(
    relationship: str,
    *,
    source: Optional[Union[str, RecordID]] = None,
    target: Optional[Union[str, RecordID]] = None,
) -> int:
    return len(await repo_relations(relationship, source=source, target=target))


async def repo_relation_exists(
    relationship: str,
    *,
    source: Optional[Union[str, RecordID]] = None,
    target: Optional[Union[str, RecordID]] = None,
) -> bool:
    return bool(await repo_relations(relationship, source=source, target=target))


async def repo_related_records(
    relationship: str,
    *,
    source: Optional[Union[str, RecordID]] = None,
    target: Optional[Union[str, RecordID]] = None,
    related_side: str,
    order_by: Optional[str] = None,
    descending: bool = False,
) -> list[Dict[str, Any]]:
    """Resolve one side of matching relation rows into records."""
    if related_side not in {"source", "target"}:
        raise ValueError("related_side must be 'source' or 'target'")
    relations = await repo_relations(relationship, source=source, target=target)
    id_field = "in" if related_side == "source" else "out"
    records: list[Dict[str, Any]] = []
    for relation in relations:
        record = await repo_get(relation[id_field])
        if record is not None:
            records.append(record)
    if order_by:
        records.sort(
            key=lambda row: (
                _row_field(row, order_by) is None,
                _row_field(row, order_by),
            ),
            reverse=descending,
        )
    return records


async def repo_delete_relations(
    relationship: str,
    *,
    source: Optional[Union[str, RecordID]] = None,
    target: Optional[Union[str, RecordID]] = None,
) -> int:
    await ensure_schema()
    clauses = ["kind=%s"]
    params: list[Any] = [relationship]
    if source is not None:
        source_id = ensure_record_id(source)
        clauses.extend(["source_table=%s", "source_key=%s"])
        params.extend([source_id.table, source_id.id])
    if target is not None:
        target_id = ensure_record_id(target)
        clauses.extend(["target_table=%s", "target_key=%s"])
        params.extend([target_id.table, target_id.id])
    async with db_connection() as connection:
        cursor = await connection.execute(
            "DELETE FROM on_relation WHERE " + " AND ".join(clauses), tuple(params)
        )
        await connection.commit()
        return int(cursor.rowcount or 0)


async def repo_upsert(
    table: str, id: Optional[str], data: Dict[str, Any], add_timestamp: bool = False
) -> list[Dict[str, Any]]:
    payload = dict(data)
    payload.pop("id", None)
    if add_timestamp:
        payload["updated"] = datetime.now(timezone.utc)
    if id:
        record_id = ensure_record_id(id) if ":" in id else RecordID(table, id)
        row = await update_record(record_id, payload, upsert=True)
    else:
        row = await create_record(table, payload)
    return [row] if row else []


async def repo_update(
    table: str, id: Union[str, RecordID], data: Dict[str, Any]
) -> list[Dict[str, Any]]:
    if isinstance(id, RecordID):
        record_id = id
    elif ":" in id:
        record_id = ensure_record_id(id)
        if record_id.table != table:
            raise ValueError(f"Record {record_id} does not belong to table {table}")
    else:
        record_id = RecordID(table, id)
    row = await update_record(
        record_id,
        normalize_json({key: value for key, value in data.items() if key != "id"}),
    )
    return [row] if row else []


async def repo_update_record(
    record_id: Union[str, RecordID], data: Mapping[str, Any]
) -> Optional[Dict[str, Any]]:
    return await update_record(ensure_record_id(record_id), normalize_json(dict(data)))


async def repo_delete(record_id: Union[str, RecordID]) -> bool:
    return await delete_record(ensure_record_id(record_id))


async def repo_delete_where(
    table: str,
    *,
    filters: Optional[Mapping[str, Any]] = None,
    in_filters: Optional[Mapping[str, Iterable[Any]]] = None,
) -> int:
    rows = await repo_list(table, filters=filters, in_filters=in_filters)
    deleted = 0
    for row in rows:
        if await repo_delete(str(row["id"])):
            deleted += 1
    return deleted


async def repo_insert(
    table: str, data: list[Dict[str, Any]], ignore_duplicates: bool = False
) -> list[Dict[str, Any]]:
    if table == "source_embedding":
        from open_notebook.database.embeddings import replace_source_embeddings

        grouped: dict[str, list[Dict[str, Any]]] = {}
        for row in data:
            source_id = ensure_record_id(row["source"])
            grouped.setdefault(str(source_id), []).append(row)
        created: list[dict[str, Any]] = []
        for source_ref, rows in grouped.items():
            created.extend(await replace_source_embeddings(ensure_record_id(source_ref), rows))
        return created

    results: list[dict[str, Any]] = []
    for payload in data:
        try:
            results.append(await create_record(table, dict(payload)))
        except Exception:
            if not ignore_duplicates:
                raise
            logger.debug(f"Ignoring duplicate insert into {table}")
    return results


async def repo_command_rows(command_ids: Sequence[Union[str, RecordID]]) -> list[Dict[str, Any]]:
    from uuid import UUID

    ids: list[UUID] = []
    for value in command_ids:
        text = str(value)
        if text.startswith("command:"):
            text = text.split(":", 1)[1]
        try:
            ids.append(UUID(text))
        except ValueError:
            continue
    if not ids:
        return []
    await ensure_schema()
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                "SELECT id, status, result, error_message, created, updated "
                "FROM command_job WHERE id = ANY(%s)",
                (ids,),
            )
        ).fetchall()
    return [
        {
            "id": f"command:{row['id']}",
            "status": row["status"],
            "result": row.get("result"),
            "error_message": row.get("error_message"),
            "created": row.get("created"),
            "updated": row.get("updated"),
        }
        for row in rows
    ]


__all__ = [
    "RecordID",
    "db_connection",
    "ensure_record_id",
    "get_database_name",
    "get_database_namespace",
    "get_database_password",
    "get_database_url",
    "parse_record_ids",
    "repo_command_rows",
    "repo_count",
    "repo_create",
    "repo_delete",
    "repo_delete_relations",
    "repo_delete_where",
    "repo_exists",
    "repo_get",
    "repo_healthcheck",
    "repo_insert",
    "repo_list",
    "repo_relate",
    "repo_related_records",
    "repo_relation_count",
    "repo_relation_exists",
    "repo_relations",
    "repo_update",
    "repo_update_record",
    "repo_upsert",
]
