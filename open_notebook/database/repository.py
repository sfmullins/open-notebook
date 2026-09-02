"""Database repository compatibility layer backed by PostgreSQL.

The public/domain contract still uses ``table:key`` identifiers.  CRUD and the
most common historical ``repo_query`` shapes are translated onto the PostgreSQL
record/relation store while remaining bespoke SurrealQL graph/search call sites
are migrated to native helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Mapping, Optional, TypeVar, Union

from loguru import logger

from open_notebook.database.postgres import (
    create_record,
    db_connection,
    delete_record,
    ensure_schema,
    get_database_url,
    list_records,
    normalize_json,
    relate,
    update_record,
)
from open_notebook.database.record_id import RecordID

T = TypeVar("T", Dict[str, Any], List[Dict[str, Any]])
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _ensure_safe_identifier(value: str, kind: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Invalid {kind} name: {value!r}")
    return value


def get_database_password() -> str:
    """Legacy accessor retained for callers; PostgreSQL credentials live in DATABASE_URL."""
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


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().rstrip(";").split())


def _value(value: Any) -> Any:
    if isinstance(value, RecordID):
        return str(value)
    return value


def _row_field(row: Mapping[str, Any], field: str) -> Any:
    current: Any = row
    for part in field.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            return None
    return current


def _compare(left: Any, right: Any) -> bool:
    if isinstance(left, RecordID):
        left = str(left)
    if isinstance(right, RecordID):
        right = str(right)
    if isinstance(right, list):
        right = [_value(item) for item in right]
    return left == right


def _condition_matches(row: Mapping[str, Any], expression: str, vars: Mapping[str, Any]) -> bool:
    expression = expression.strip().strip("()")

    # Handle simple boolean composition used by current repository call sites.
    or_parts = re.split(r"\s+OR\s+", expression, flags=re.IGNORECASE)
    if len(or_parts) > 1:
        return any(_condition_matches(row, part, vars) for part in or_parts)
    and_parts = re.split(r"\s+AND\s+", expression, flags=re.IGNORECASE)
    if len(and_parts) > 1:
        return all(_condition_matches(row, part, vars) for part in and_parts)

    match = re.fullmatch(
        r"string::lowercase\((\w+)\)\s*=\s*(?:string::lowercase\()?\$(\w+)\)?",
        expression,
        flags=re.IGNORECASE,
    )
    if match:
        left = str(_row_field(row, match.group(1)) or "").lower()
        right = str(vars.get(match.group(2)) or "").lower()
        return left == right

    match = re.fullmatch(r"string::trim\((\w+)\)\s*!=\s*''", expression, re.IGNORECASE)
    if match:
        return bool(str(_row_field(row, match.group(1)) or "").strip())

    match = re.fullmatch(r"array::len\((\w+)\)\s*>\s*0", expression, re.IGNORECASE)
    if match:
        value = _row_field(row, match.group(1))
        return isinstance(value, list) and len(value) > 0

    match = re.fullmatch(r"([\w.]+)\s+IS\s+NONE", expression, re.IGNORECASE)
    if match:
        return _row_field(row, match.group(1)) is None

    match = re.fullmatch(r"([\w.]+)\s*!=\s*(?:NONE|NULL)", expression, re.IGNORECASE)
    if match:
        return _row_field(row, match.group(1)) is not None

    match = re.fullmatch(r"([\w.]+)\s+IN\s+\$(\w+)", expression, re.IGNORECASE)
    if match:
        values = [_value(item) for item in vars.get(match.group(2), [])]
        return _value(_row_field(row, match.group(1))) in values

    match = re.fullmatch(r"([\w.]+)\s*(!=|=)\s*\$(\w+)", expression)
    if match:
        equal = _compare(_row_field(row, match.group(1)), vars.get(match.group(3)))
        return not equal if match.group(2) == "!=" else equal

    match = re.fullmatch(r"([\w.]+)\s*(!=|=)\s*'(.*)'", expression)
    if match:
        equal = str(_row_field(row, match.group(1)) or "") == match.group(3)
        return not equal if match.group(2) == "!=" else equal

    raise NotImplementedError(f"Unsupported PostgreSQL compatibility condition: {expression}")


def _projection(rows: list[dict[str, Any]], projection: str) -> list[Any]:
    projection = projection.strip()
    value_mode = False
    if projection.upper().startswith("VALUE "):
        value_mode = True
        projection = projection[6:].strip()
    if projection == "*":
        return rows

    expressions = [part.strip() for part in projection.split(",")]
    projected: list[Any] = []
    for row in rows:
        out: dict[str, Any] = {}
        for expression in expressions:
            lower = re.fullmatch(
                r"string::lowercase\((\w+)\)\s+as\s+(\w+)",
                expression,
                re.IGNORECASE,
            )
            if lower:
                out[lower.group(2)] = str(row.get(lower.group(1)) or "").lower()
                continue
            alias = re.fullmatch(r"([\w.]+)\s+as\s+(\w+)", expression, re.IGNORECASE)
            if alias:
                out[alias.group(2)] = _row_field(row, alias.group(1))
                continue
            out[expression] = _row_field(row, expression)
        projected.append(next(iter(out.values())) if value_mode and len(out) == 1 else out)
    return projected


def _resolve_limit(value: str | None, vars: Mapping[str, Any], default: int) -> int:
    if not value:
        return default
    if value.startswith("$"):
        return int(vars.get(value[1:], default))
    return int(value)


async def _select_records(query: str, vars: Mapping[str, Any]) -> list[Any]:
    match = re.fullmatch(
        r"SELECT\s+(.+?)\s+FROM\s+(\w+)"
        r"(?:\s+WHERE\s+(.+?))?"
        r"(?:\s+GROUP\s+(?:ALL|BY\s+.+?))?"
        r"(?:\s+ORDER\s+BY\s+(.+?))?"
        r"(?:\s+LIMIT\s+(\$?\w+|\d+))?"
        r"(?:\s+START\s+(\$?\w+|\d+))?",
        query,
        re.IGNORECASE,
    )
    if not match:
        raise NotImplementedError(f"Unsupported PostgreSQL compatibility SELECT: {query}")
    projection, table, where, order_by, limit_token, start_token = match.groups()
    rows = await list_records(table)
    if where:
        rows = [row for row in rows if _condition_matches(row, where, vars)]

    if re.search(r"\bcount\(\)", projection, re.IGNORECASE):
        return [{"count": len(rows)}]

    if order_by:
        first_order = order_by.split(",", 1)[0].strip().split()
        field = first_order[0]
        reverse = len(first_order) > 1 and first_order[1].lower() == "desc"
        rows.sort(key=lambda row: (_row_field(row, field) is None, _row_field(row, field)), reverse=reverse)

    start = _resolve_limit(start_token, vars, 0)
    limit = _resolve_limit(limit_token, vars, len(rows))
    rows = rows[start : start + limit]
    return _projection(rows, projection)


async def _relation_rows(kind: str) -> list[dict[str, Any]]:
    _ensure_safe_identifier(kind, "relationship")
    await ensure_schema()
    async with db_connection() as connection:
        rows = await (
            await connection.execute(
                """
                SELECT id, kind, source_table, source_key, target_table, target_key, data, created
                FROM on_relation WHERE kind=%s
                """,
                (kind,),
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


async def repo_query(query_str: str, vars: Optional[Dict[str, Any]] = None) -> List[Any]:
    """Execute a supported legacy repository query on PostgreSQL.

    New code should use native repository helpers.  This compatibility path is
    intentionally explicit: an unported complex SurrealQL query raises instead
    of silently returning incorrect data.
    """
    params: Dict[str, Any] = vars or {}
    query = _normalize_query(query_str)
    upper = query.upper()

    if upper in {"RETURN 1", "RETURN TRUE"}:
        return [1 if upper == "RETURN 1" else True]

    direct = re.fullmatch(r"SELECT \* FROM(?: ONLY)? \$(\w+)", query, re.IGNORECASE)
    if direct:
        record = await get_record_compat(ensure_record_id(params[direct.group(1)]))
        return [record] if record else []

    relation_select = re.fullmatch(
        r"SELECT (.+?) FROM (\w+) WHERE (.+)", query, re.IGNORECASE
    )
    if relation_select and relation_select.group(2) in {"reference", "artifact", "refers_to"}:
        projection, kind, where = relation_select.groups()
        rows = await _relation_rows(kind)
        rows = [row for row in rows if _condition_matches(row, where, params)]
        return _projection(rows, projection)

    relate_match = re.fullmatch(
        r"RELATE \$(\w+)->(\w+)->\$(\w+)(?: CONTENT \$(\w+))?",
        query,
        re.IGNORECASE,
    )
    if relate_match:
        source_name, kind, target_name, data_name = relate_match.groups()
        row = await repo_relate(
            params[source_name], kind, params[target_name], params.get(data_name, {}) if data_name else {}
        )
        return row

    update_match = re.fullmatch(
        r"UPDATE \$(\w+) SET (\w+) = (\$\w+|time::now\(\))",
        query,
        re.IGNORECASE,
    )
    if update_match:
        id_name, field, value_token = update_match.groups()
        value = (
            datetime.now(timezone.utc)
            if value_token.lower().startswith("time::now")
            else params[value_token[1:]]
        )
        row = await update_record(ensure_record_id(params[id_name]), {field: value})
        return [row] if row else []

    create_insight = re.fullmatch(r"CREATE source_insight CONTENT \{.+\}", query, re.IGNORECASE)
    if create_insight:
        data = {
            "source": _value(params.get("source_id")),
            "insight_type": params.get("insight_type"),
            "content": params.get("content"),
        }
        row = await repo_create("source_insight", data)
        return [row]

    delete_relation = re.fullmatch(r"DELETE(?: FROM)? (reference|artifact|refers_to) WHERE (.+)", query, re.IGNORECASE)
    if delete_relation:
        kind, where = delete_relation.groups()
        rows = await _relation_rows(kind)
        matches = [row for row in rows if _condition_matches(row, where, params)]
        if matches:
            async with db_connection() as connection:
                for row in matches:
                    relation_id = ensure_record_id(row["id"]).id
                    await connection.execute("DELETE FROM on_relation WHERE id=%s", (relation_id,))
                await connection.commit()
        return matches

    if upper.startswith("SELECT "):
        return await _select_records(query, params)

    raise NotImplementedError(
        "Legacy SurrealQL call has not yet been ported to PostgreSQL: " + query[:240]
    )


async def get_record_compat(record_id: RecordID) -> Optional[dict[str, Any]]:
    from open_notebook.database.postgres import get_record

    return await get_record(record_id)


async def repo_create(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_safe_identifier(table, "table")
    payload = dict(data)
    payload.pop("id", None)
    return await create_record(table, payload)


async def repo_relate(
    source: Union[str, RecordID],
    relationship: str,
    target: Union[str, RecordID],
    data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    _ensure_safe_identifier(relationship, "relationship")
    row = await relate(
        ensure_record_id(source),
        relationship,
        ensure_record_id(target),
        data or {},
    )
    return [row]


async def repo_upsert(
    table: str, id: Optional[str], data: Dict[str, Any], add_timestamp: bool = False
) -> List[Dict[str, Any]]:
    _ensure_safe_identifier(table, "table")
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
) -> List[Dict[str, Any]]:
    _ensure_safe_identifier(table, "table")
    if isinstance(id, RecordID):
        record_id = id
    elif ":" in id:
        record_id = ensure_record_id(id)
        if record_id.table != table:
            raise ValueError(f"Record {record_id} does not belong to table {table}")
    else:
        record_id = RecordID(table, id)
    payload = normalize_json({key: value for key, value in data.items() if key != "id"})
    row = await update_record(record_id, payload)
    return [row] if row else []


async def repo_delete(record_id: Union[str, RecordID]) -> bool:
    return await delete_record(ensure_record_id(record_id))


async def repo_insert(
    table: str, data: List[Dict[str, Any]], ignore_duplicates: bool = False
) -> List[Dict[str, Any]]:
    _ensure_safe_identifier(table, "table")
    results: list[dict[str, Any]] = []
    for payload in data:
        try:
            results.append(await create_record(table, dict(payload)))
        except Exception:
            if not ignore_duplicates:
                raise
            logger.debug(f"Ignoring duplicate insert into {table}")
    return results
