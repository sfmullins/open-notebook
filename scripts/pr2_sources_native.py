#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "api/routers/sources.py"
text = PATH.read_text(encoding="utf-8")


def one(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    text = text.replace(old, new, 1)


def block(start: str, end: str, replacement: str, label: str) -> None:
    global text
    s = text.find(start)
    if s < 0:
        raise RuntimeError(f"{label}: start missing")
    e = text.find(end, s)
    if e < 0:
        raise RuntimeError(f"{label}: end missing")
    text = text[:s] + replacement + text[e:]


one(
    "from open_notebook.database.repository import ensure_record_id, repo_query\n",
    "from open_notebook.database.embeddings import count_source_embeddings\n"
    "from open_notebook.database.repository import (\n"
    "    ensure_record_id,\n"
    "    repo_command_rows,\n"
    "    repo_count,\n"
    "    repo_list,\n"
    "    repo_related_records,\n"
    "    repo_relations,\n"
    "    repo_update_record,\n"
    ")\n",
    "repository import",
)

# Remove Surreal-specific sort implementation constants.
block(
    "SOURCE_SORT_FIELDS = {\n",
    "\n\nasync def _stamp_source_view(source_id: str) -> None:\n",
    '''SOURCE_SORT_FIELDS = {
    "created",
    "updated",
    "title",
    "insights_count",
    "embedded",
    "type",
}


def _source_type(row: dict[str, Any]) -> str:
    asset = row.get("asset") or {}
    if asset.get("file_path"):
        return "file"
    if asset.get("url"):
        return "link"
    return "text"


def _source_sort_value(row: dict[str, Any], field: str) -> Any:
    if field == "title":
        return str(row.get("title") or "").lower()
    if field == "type":
        return _source_type(row)
    if field == "insights_count":
        return int(row.get("insights_count") or 0)
    if field == "embedded":
        return bool(row.get("embedded"))
    return row.get(field)

''',
    "sort constants",
)

block(
    "async def _stamp_source_view(source_id: str) -> None:\n",
    "\n\ndef generate_unique_filename",
    '''async def _stamp_source_view(source_id: str) -> None:
    # Best-effort write-on-read; a failed view stamp must not fail the read.
    try:
        from datetime import datetime, timezone

        await repo_update_record(
            source_id, {"last_viewed_at": datetime.now(timezone.utc)}
        )
    except Exception as e:
        logger.warning(f"Failed to stamp last_viewed_at for source {source_id}: {e}")
''',
    "source view stamp",
)

block(
    '@router.get("/sources", response_model=List[SourceListResponse])\n',
    "\n\ndef _source_to_response(\n",
    '''@router.get("/sources", response_model=List[SourceListResponse])
async def get_sources(
    notebook_id: Optional[str] = Query(None, description="Filter by notebook ID"),
    limit: int = Query(
        50, ge=1, le=100, description="Number of sources to return (1-100)"
    ),
    offset: int = Query(0, ge=0, description="Number of sources to skip"),
    sort_by: str = Query(
        "updated",
        description="Field to sort by (type, title, created, updated, insights_count, or embedded)",
    ),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),
):
    """Get sources using structured repository operations only."""
    try:
        if sort_by not in SOURCE_SORT_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "sort_by must be one of: type, title, created, updated, "
                    "insights_count, embedded"
                ),
            )
        descending = sort_order.lower() == "desc"
        if sort_order.lower() not in {"asc", "desc"}:
            raise HTTPException(status_code=400, detail="sort_order must be 'asc' or 'desc'")

        if notebook_id:
            await Notebook.get(notebook_id)
            rows = await repo_related_records(
                "reference", target=notebook_id, related_side="source"
            )
        else:
            rows = await repo_list("source")

        # Compute fields that previously came from nested SurrealQL subqueries.
        for row in rows:
            source_id = str(row.get("id", ""))
            row["insights_count"] = await repo_count(
                "source_insight", filters={"source": source_id}
            )
            row["embedded"] = (
                await count_source_embeddings(ensure_record_id(source_id)) > 0
            )
            row["type"] = _source_type(row)

        # Preserve deterministic id tie-breaking, then apply requested sort.
        rows.sort(key=lambda row: str(row.get("id", "")))
        rows.sort(
            key=lambda row: (
                _source_sort_value(row, sort_by) is None,
                _source_sort_value(row, sort_by),
            ),
            reverse=descending,
        )
        rows = rows[offset : offset + limit]

        command_ids = [str(row["command"]) for row in rows if row.get("command")]
        command_map = {
            str(row.get("id")): row for row in await repo_command_rows(command_ids)
        }

        response_list = []
        for row in rows:
            command_ref = str(row.get("command")) if row.get("command") else None
            command = command_map.get(command_ref or "")
            command_id = command_ref
            status = None
            processing_info = None
            if command:
                status = command.get("status")
                result_data = command.get("result")
                execution_metadata = (
                    result_data.get("execution_metadata", {})
                    if isinstance(result_data, dict)
                    else {}
                )
                processing_info = {
                    "started_at": execution_metadata.get("started_at"),
                    "completed_at": execution_metadata.get("completed_at"),
                    "error": _truncate_error(command.get("error_message")),
                }
            elif command_ref:
                status = "unknown"

            response_list.append(
                SourceListResponse(
                    id=str(row.get("id", "")),
                    title=row.get("title"),
                    topics=row.get("topics") or [],
                    asset=AssetModel(
                        file_path=(row.get("asset") or {}).get("file_path"),
                        url=(row.get("asset") or {}).get("url"),
                    )
                    if row.get("asset")
                    else None,
                    embedded=bool(row.get("embedded")),
                    embedded_chunks=0,
                    insights_count=int(row.get("insights_count") or 0),
                    created=str(row.get("created", "")),
                    updated=str(row.get("updated", "")),
                    command_id=command_id,
                    status=status,
                    processing_info=processing_info,
                )
            )
        return response_list
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching sources: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching sources")
''',
    "get_sources",
)

one(
    '''        notebooks_query = await repo_query(
            "SELECT VALUE out FROM reference WHERE in = $source_id",
            {"source_id": ensure_record_id(source.id or source_id)},
        )
        notebook_ids = (
            [str(nb_id) for nb_id in notebooks_query] if notebooks_query else []
        )
''',
    '''        notebook_relations = await repo_relations(
            "reference", source=source.id or source_id
        )
        notebook_ids = [str(relation["out"]) for relation in notebook_relations]
''',
    "get source notebook relations",
)

one(
    '''        references = await repo_query(
            "SELECT VALUE out FROM reference WHERE in = $source_id",
            {"source_id": ensure_record_id(source.id or source_id)},
        )
        notebook_ids = [str(nb_id) for nb_id in references] if references else []
''',
    '''        references = await repo_relations(
            "reference", source=source.id or source_id
        )
        notebook_ids = [str(relation["out"]) for relation in references]
''',
    "retry source notebook relations",
)

PATH.write_text(text, encoding="utf-8")

for rel in (
    "scripts/pr2_sources_native.py",
    ".github/workflows/pr2-sources-native.yml",
):
    target = ROOT / rel
    if target.exists():
        target.unlink()
