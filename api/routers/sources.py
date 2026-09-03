import asyncio
import os
from pathlib import Path
from typing import Any, List, Optional

from content_core import check_file_support
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response
from loguru import logger
from pydantic import ValidationError

from api.command_service import CommandService
from api.credentials_service import validate_url
from api.models import (
    AssetModel,
    CreateSourceInsightRequest,
    InsightCreationResponse,
    SourceCreate,
    SourceInsightResponse,
    SourceListResponse,
    SourceResponse,
    SourceStatusResponse,
    SourceUpdate,
)
from command_queue import execute_command_sync, submit_command
from commands.source_commands import SourceProcessingInput
from open_notebook.config import UPLOADS_FOLDER
from open_notebook.database.embeddings import count_source_embeddings
from open_notebook.database.repository import (
    ensure_record_id,
    repo_command_rows,
    repo_count,
    repo_list,
    repo_related_records,
    repo_relations,
    repo_update_record,
)
from open_notebook.domain.notebook import Asset, Notebook, Source
from open_notebook.domain.transformation import Transformation
from open_notebook.exceptions import (
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
    UnsupportedTypeException,
)

router = APIRouter()


async def _assert_file_supported(file_path: str) -> None:
    """Pre-flight check that content-core can actually extract this file.

    Rejects unsupported uploads at ingestion time (HTTP 415) instead of letting
    them enqueue a background job that fails and then burns the full retry
    budget before surfacing a generic error (#975). Uses content-core's
    header-only routing, which is the same logic real extraction uses, so the
    verdict can't disagree with what would happen downstream.

    Unexpected errors (e.g. the file no longer exists on a retry) are swallowed
    so this never turns a transient problem into a hard rejection — real
    extraction will surface those.
    """
    try:
        support = await check_file_support(file_path)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Pre-flight file-support check skipped for {file_path}: {e}")
        return

    if not support.supported:
        detail = support.reason or "Unsupported file type"
        if support.identified_type:
            detail = f"{detail} (detected type: {support.identified_type})"
        raise UnsupportedTypeException(detail)


def _truncate_error(msg: Optional[str], limit: int = 200) -> Optional[str]:
    """Cap error text surfaced to clients.

    Command/processing failures can carry arbitrary internal exception text;
    return at most ``limit`` characters so a raw traceback message can't leak
    to the API response. ``None`` passes through unchanged.
    """
    if not msg:
        return msg
    return msg if len(msg) <= limit else msg[:limit] + "…"


SOURCE_SORT_FIELDS = {
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


async def _stamp_source_view(source_id: str) -> None:
    # Best-effort write-on-read; a failed view stamp must not fail the read.
    try:
        from datetime import datetime, timezone

        await repo_update_record(
            source_id, {"last_viewed_at": datetime.now(timezone.utc)}
        )
    except Exception as e:
        logger.warning(f"Failed to stamp last_viewed_at for source {source_id}: {e}")


def generate_unique_filename(original_filename: str, upload_folder: str) -> str:
    """Generate unique filename like Streamlit app (append counter if file exists),
    atomically reserving it so two concurrent uploads that land on the same
    candidate name can't both pass the check and then clobber each other -
    the loser's claim attempt fails and moves on to the next candidate."""
    file_path = Path(upload_folder)
    file_path.mkdir(parents=True, exist_ok=True)

    # Strip directory components to prevent path traversal
    safe_filename = os.path.basename(original_filename)
    if not safe_filename:
        raise ValueError("Invalid filename")

    # Split filename and extension
    stem = Path(safe_filename).stem
    suffix = Path(safe_filename).suffix
    safe_root = file_path.resolve()

    # Find and atomically claim a unique name
    counter = 0
    while True:
        if counter == 0:
            new_filename = safe_filename
        else:
            new_filename = f"{stem} ({counter}){suffix}"

        full_path = file_path / new_filename
        # Verify resolved path stays within upload folder
        resolved = full_path.resolve()
        if not str(resolved).startswith(str(safe_root) + os.sep):
            raise ValueError("Invalid filename: path traversal detected")

        try:
            # O_EXCL via touch(exist_ok=False): atomically create-or-fail,
            # instead of exists() (check) followed by a separate write
            # (act) elsewhere with a race window in between.
            resolved.touch(exist_ok=False)
            return str(resolved)
        except FileExistsError:
            counter += 1


def _write_uploaded_file(filename: str, content: bytes) -> str:
    """Sync filesystem work for save_uploaded_file() - run via asyncio.to_thread
    so a large upload doesn't block the event loop for other requests."""
    file_path = generate_unique_filename(filename, UPLOADS_FOLDER)
    try:
        with open(file_path, "wb") as f:
            f.write(content)

        logger.info(f"Saved uploaded file to: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        # Clean up partial file if it exists
        if os.path.exists(file_path):
            os.unlink(file_path)
        raise


async def save_uploaded_file(upload_file: UploadFile) -> str:
    """Save uploaded file to uploads folder and return file path."""
    if not upload_file.filename:
        raise ValueError("No filename provided")

    content = await upload_file.read()
    return await asyncio.to_thread(_write_uploaded_file, upload_file.filename, content)


def parse_source_form_data(
    type: str = Form(...),
    notebook_id: Optional[str] = Form(None),
    notebooks: Optional[str] = Form(None),  # JSON string of notebook IDs
    url: Optional[str] = Form(None),
    content: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    transformations: Optional[str] = Form(None),  # JSON string of transformation IDs
    embed: str = Form("false"),  # Accept as string, convert to bool
    delete_source: str = Form("false"),  # Accept as string, convert to bool
    async_processing: str = Form("false"),  # Accept as string, convert to bool
    file: Optional[UploadFile] = File(None),
) -> tuple[SourceCreate, Optional[UploadFile]]:
    """Parse form data into SourceCreate model and return upload file separately."""
    import json

    # Convert string booleans to actual booleans
    def str_to_bool(value: str) -> bool:
        return value.lower() in ("true", "1", "yes", "on")

    embed_bool = str_to_bool(embed)
    delete_source_bool = str_to_bool(delete_source)
    async_processing_bool = str_to_bool(async_processing)

    # Parse JSON strings
    notebooks_list = None
    if notebooks:
        try:
            notebooks_list = json.loads(notebooks)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in notebooks field: {notebooks}")
            raise HTTPException(
                status_code=422, detail="Invalid JSON in notebooks field"
            )

    transformations_list = []
    if transformations:
        try:
            transformations_list = json.loads(transformations)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in transformations field: {transformations}")
            raise HTTPException(
                status_code=422, detail="Invalid JSON in transformations field"
            )

    # Create SourceCreate instance
    try:
        source_data = SourceCreate(
            type=type,
            notebook_id=notebook_id,
            notebooks=notebooks_list,
            url=url,
            content=content,
            title=title,
            file_path=None,  # Will be set later if file is uploaded
            transformations=transformations_list,
            embed=embed_bool,
            delete_source=delete_source_bool,
            async_processing=async_processing_bool,
        )
    except ValidationError as e:
        errors = "; ".join(err.get("msg", "invalid value") for err in e.errors())
        logger.error(f"Invalid source form data: {errors}")
        raise HTTPException(status_code=422, detail=f"Invalid source data: {errors}")
    except Exception as e:
        logger.error(f"Failed to create SourceCreate instance: {e}")
        raise

    return source_data, file


@router.get("/sources", response_model=List[SourceListResponse])
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
            raise HTTPException(
                status_code=400, detail="sort_order must be 'asc' or 'desc'"
            )

        if notebook_id:
            await Notebook.get(notebook_id)
            rows = await repo_related_records(
                "reference", target=notebook_id, related_side="source"
            )
        else:
            rows = await repo_list("source")

        for row in rows:
            source_id = str(row.get("id", ""))
            row["insights_count"] = await repo_count(
                "source_insight", filters={"source": source_id}
            )
            row["embedded"] = (
                await count_source_embeddings(ensure_record_id(source_id)) > 0
            )
            row["type"] = _source_type(row)

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


def _source_to_response(
    source: Source, embedded_chunks: int = 0, **extras: Any
) -> SourceResponse:
    """Build a SourceResponse from a Source, deriving the shared fields.

    Endpoint-specific fields (command_id, status, processing_info, notebooks,
    file_available, ...) are passed as keyword arguments and override the
    derived values.
    """
    fields: dict[str, Any] = {
        "id": source.id or "",
        "title": source.title,
        "topics": source.topics or [],
        "asset": AssetModel(
            file_path=source.asset.file_path,
            url=source.asset.url,
        )
        if source.asset
        else None,
        "full_text": source.full_text,
        "embedded": embedded_chunks > 0,
        "embedded_chunks": embedded_chunks,
        "created": str(source.created),
        "updated": str(source.updated),
    }
    fields.update(extras)
    return SourceResponse(**fields)


def _cleanup_uploaded_file(
    file_path: Optional[str], upload_file: Optional[UploadFile]
) -> None:
    """Best-effort removal of a file this request uploaded, after a failure.

    Only removes files we created ourselves (an upload_file was provided) -
    never a caller-supplied file_path."""
    if file_path and upload_file:
        try:
            os.unlink(file_path)
        except Exception:
            pass


async def _build_content_state(
    source_data: SourceCreate, file_path: Optional[str]
) -> dict[str, Any]:
    """Validate the type-specific input and build the content_state passed to
    the processing command. The SSRF and LFI guards live here."""
    content_state: dict[str, Any] = {}

    if source_data.type == "link":
        if not source_data.url:
            raise HTTPException(status_code=400, detail="URL is required for link type")
        # Block SSRF to internal/metadata addresses before the server ever
        # fetches this URL (same guard used for provider-credential URLs).
        try:
            await validate_url(source_data.url, "source")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        content_state["url"] = source_data.url
    elif source_data.type == "upload":
        # Use uploaded file path or provided file_path (backward compatibility)
        final_file_path = file_path or source_data.file_path
        if not final_file_path:
            raise HTTPException(
                status_code=400,
                detail="File upload or file_path is required for upload type",
            )
        # Validate file_path is within the uploads directory to prevent LFI
        uploads_resolved = Path(UPLOADS_FOLDER).resolve()
        file_resolved = Path(final_file_path).resolve()
        if not str(file_resolved).startswith(str(uploads_resolved) + os.sep):
            raise HTTPException(
                status_code=400,
                detail="Invalid file path: must be within the uploads directory",
            )
        # Reject unsupported files before enqueueing a doomed background job.
        await _assert_file_supported(final_file_path)
        content_state["file_path"] = final_file_path
        content_state["delete_source"] = source_data.delete_source
    elif source_data.type == "text":
        if not source_data.content:
            raise HTTPException(
                status_code=400, detail="Content is required for text type"
            )
        content_state["content"] = source_data.content
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid source type. Must be link, upload, or text",
        )

    return content_state


async def _create_source_async_path(
    source_data: SourceCreate,
    content_state: dict[str, Any],
    transformation_ids: List[str],
    file_path: Optional[str],
) -> SourceResponse:
    """ASYNC PATH: Create source record first, then queue command."""
    logger.info("Using async processing path")

    # Create source record with asset - let the repository generate the ID
    # Persist asset before save so it's available for retry if processing fails
    if source_data.type == "link":
        source_asset = Asset(url=source_data.url)
    elif source_data.type == "upload":
        source_asset = Asset(file_path=file_path or source_data.file_path)
    else:
        source_asset = None

    source = Source(
        title=source_data.title or "Processing...",
        topics=[],
        asset=source_asset,
    )
    await source.save()

    # Add source to notebooks immediately so it appears in the UI
    # The source_graph will skip adding duplicates
    for notebook_id in source_data.notebooks or []:
        await source.add_to_notebook(notebook_id)

    try:
        # Import command modules to ensure they're registered
        import commands.source_commands  # noqa: F401

        # Submit command for background processing
        command_input = SourceProcessingInput(
            source_id=str(source.id),
            content_state=content_state,
            notebook_ids=source_data.notebooks,
            transformations=transformation_ids,
            embed=source_data.embed,
        )

        command_id = await CommandService.submit_command_job(
            "open_notebook",  # app name
            "process_source",  # command name
            command_input.model_dump(),
        )

        logger.info(f"Submitted async processing command: {command_id}")

        # Update source with command reference immediately
        # command_id already includes 'command:' prefix
        source.command = ensure_record_id(command_id)
        await source.save()

        # Return source with command info
        return _source_to_response(
            source,
            asset=None,  # Will be populated after processing
            full_text=None,  # Will be populated after processing
            embedded=False,  # Will be updated after processing
            embedded_chunks=0,
            command_id=command_id,
            status="new",
            processing_info={"async": True, "queued": True},
        )

    except (HTTPException, OpenNotebookError):
        # Clean up source record before the error propagates (typed domain
        # errors are mapped by the global handlers in api/main.py)
        try:
            await source.delete()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"Failed to submit async processing command: {e}")
        # Clean up source record on command submission failure
        try:
            await source.delete()
        except Exception:
            pass
        # The uploaded file (if any) is cleaned up by create_source's handlers
        raise HTTPException(status_code=500, detail="Failed to queue processing")


async def _create_source_sync_path(
    source_data: SourceCreate,
    content_state: dict[str, Any],
    transformation_ids: List[str],
) -> SourceResponse:
    """SYNC PATH: Execute synchronously using execute_command_sync."""
    logger.info("Using sync processing path")

    try:
        # Import command modules to ensure they're registered
        import commands.source_commands  # noqa: F401

        # Create source record - let the repository generate the ID
        source = Source(
            title=source_data.title or "Processing...",
            topics=[],
        )
        await source.save()

        # Add source to notebooks immediately so it appears in the UI
        # The source_graph will skip adding duplicates
        for notebook_id in source_data.notebooks or []:
            await source.add_to_notebook(notebook_id)

        # Execute command synchronously
        command_input = SourceProcessingInput(
            source_id=str(source.id),
            content_state=content_state,
            notebook_ids=source_data.notebooks,
            transformations=transformation_ids,
            embed=source_data.embed,
        )

        # Run in thread pool to avoid blocking the event loop
        # execute_command_sync uses asyncio.run() internally which can't
        # be called from an already-running event loop (FastAPI)
        result = await asyncio.to_thread(
            execute_command_sync,
            "open_notebook",  # app name
            "process_source",  # command name
            command_input.model_dump(),
            timeout=300,  # 5 minute timeout for sync processing
        )

        if not result.is_success():
            logger.error(f"Sync processing failed: {result.error_message}")
            # Clean up source record
            try:
                await source.delete()
            except Exception:
                pass
            raise HTTPException(
                status_code=500,
                detail=f"Processing failed: {_truncate_error(result.error_message)}",
            )

        # Get the processed source
        if not source.id:
            raise HTTPException(status_code=500, detail="Source ID is missing")
        processed_source = await Source.get(source.id)
        if not processed_source:
            raise HTTPException(status_code=500, detail="Processed source not found")

        embedded_chunks = await processed_source.get_embedded_chunks()
        # No command_id or status for sync processing (legacy behavior)
        return _source_to_response(processed_source, embedded_chunks=embedded_chunks)

    except Exception as e:
        logger.error(f"Sync processing failed: {e}")
        # The uploaded file (if any) is cleaned up by create_source's handlers
        raise


@router.post("/sources", response_model=SourceResponse)
async def create_source(
    form_data: tuple[SourceCreate, Optional[UploadFile]] = Depends(
        parse_source_form_data
    ),
):
    """Create a new source with support for both JSON and multipart form data."""
    source_data, upload_file = form_data

    # Initialize file_path before try block so exception handlers can reference it
    file_path = None

    try:
        # Verify all specified notebooks exist (backward compatibility support)
        for notebook_id in source_data.notebooks or []:
            notebook = await Notebook.get(notebook_id)
            if not notebook:
                raise HTTPException(
                    status_code=404, detail=f"Notebook {notebook_id} not found"
                )

        # Handle file upload if provided
        if upload_file and source_data.type == "upload":
            try:
                file_path = await save_uploaded_file(upload_file)
            except Exception as e:
                logger.error(f"File upload failed: {e}")
                raise HTTPException(status_code=400, detail="File upload failed")

        # Prepare content_state for processing (type validation + SSRF/LFI guards)
        content_state = await _build_content_state(source_data, file_path)

        # Validate transformations exist
        transformation_ids = source_data.transformations or []
        for trans_id in transformation_ids:
            transformation = await Transformation.get(trans_id)
            if not transformation:
                raise HTTPException(
                    status_code=404, detail=f"Transformation {trans_id} not found"
                )

        # Branch based on processing mode
        if source_data.async_processing:
            return await _create_source_async_path(
                source_data, content_state, transformation_ids, file_path
            )
        return await _create_source_sync_path(
            source_data, content_state, transformation_ids
        )

    except HTTPException:
        # Clean up uploaded file on HTTP exceptions if we created it
        _cleanup_uploaded_file(file_path, upload_file)
        raise
    except InvalidInputError as e:
        # Clean up uploaded file on validation errors if we created it
        _cleanup_uploaded_file(file_path, upload_file)
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        # Clean up uploaded file before the global handlers map the error
        _cleanup_uploaded_file(file_path, upload_file)
        raise
    except Exception as e:
        logger.error(f"Error creating source: {str(e)}")
        # Clean up uploaded file on unexpected errors if we created it
        _cleanup_uploaded_file(file_path, upload_file)
        raise HTTPException(status_code=500, detail="Error creating source")


@router.post("/sources/json", response_model=SourceResponse)
async def create_source_json(source_data: SourceCreate):
    """Create a new source using JSON payload (legacy endpoint for backward compatibility)."""
    # Convert to form data format and call main endpoint
    form_data = (source_data, None)
    return await create_source(form_data)


async def _resolve_source_file(source_id: str) -> tuple[str, str]:
    source = await Source.get(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    file_path = source.asset.file_path if source.asset else None
    if not file_path:
        raise HTTPException(status_code=404, detail="Source has no file to download")

    safe_root = os.path.realpath(UPLOADS_FOLDER)
    resolved_path = os.path.realpath(file_path)

    if resolved_path != safe_root and not resolved_path.startswith(safe_root + os.sep):
        logger.warning(
            f"Blocked download outside uploads directory for source {source_id}: {resolved_path}"
        )
        raise HTTPException(status_code=403, detail="Access to file denied")

    if not os.path.exists(resolved_path):
        raise HTTPException(status_code=404, detail="File not found on server")

    filename = os.path.basename(resolved_path)
    return resolved_path, filename


def _is_source_file_available(source: Source) -> Optional[bool]:
    if not source or not source.asset or not source.asset.file_path:
        return None

    file_path = source.asset.file_path
    safe_root = os.path.realpath(UPLOADS_FOLDER)
    resolved_path = os.path.realpath(file_path)

    if resolved_path != safe_root and not resolved_path.startswith(safe_root + os.sep):
        return False

    return os.path.exists(resolved_path)


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(source_id: str):
    """Get a specific source by ID."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        await _stamp_source_view(source.id or source_id)

        # Get status information if command exists
        status = None
        processing_info = None
        if source.command:
            try:
                status = await source.get_status()
                processing_info = await source.get_processing_progress()
            except Exception as e:
                logger.warning(f"Failed to get status for source {source_id}: {e}")
                status = "unknown"

        embedded_chunks = await source.get_embedded_chunks()

        # Get associated notebooks
        notebook_relations = await repo_relations(
            "reference", source=source.id or source_id
        )
        notebook_ids = [str(relation["out"]) for relation in notebook_relations]

        return _source_to_response(
            source,
            embedded_chunks=embedded_chunks,
            file_available=_is_source_file_available(source),
            # Status fields
            command_id=str(source.command) if source.command else None,
            status=status,
            processing_info=processing_info,
            # Notebook associations
            notebooks=notebook_ids,
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Source not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching source")


@router.head("/sources/{source_id}/download")
async def check_source_file(source_id: str):
    """Check if a source has a downloadable file."""
    try:
        await _resolve_source_file(source_id)
        return Response(status_code=200)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error checking file for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to verify file")


@router.get("/sources/{source_id}/download")
async def download_source_file(source_id: str):
    """Download the original file associated with an uploaded source."""
    try:
        resolved_path, filename = await _resolve_source_file(source_id)
        return FileResponse(
            path=resolved_path,
            filename=filename,
            media_type="application/octet-stream",
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error downloading file for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to download source file")


@router.get("/sources/{source_id}/status", response_model=SourceStatusResponse)
async def get_source_status(source_id: str):
    """Get processing status for a source."""
    try:
        # First, verify source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Check if this is a legacy source (no command)
        if not source.command:
            return SourceStatusResponse(
                status=None,
                message="Legacy source (completed before async processing)",
                processing_info=None,
                command_id=None,
            )

        # Get command status and processing info
        try:
            status = await source.get_status()
            processing_info = await source.get_processing_progress()

            # Generate descriptive message based on status
            if status == "completed":
                message = "Source processing completed successfully"
            elif status == "failed":
                message = "Source processing failed"
            elif status == "running":
                message = "Source processing in progress"
            elif status == "queued":
                message = "Source processing queued"
            elif status == "unknown":
                message = "Source processing status unknown"
            else:
                message = f"Source processing status: {status}"

            return SourceStatusResponse(
                status=status,
                message=message,
                processing_info=processing_info,
                command_id=str(source.command) if source.command else None,
            )

        except Exception as e:
            logger.warning(f"Failed to get status for source {source_id}: {e}")
            return SourceStatusResponse(
                status="unknown",
                message="Failed to retrieve processing status",
                processing_info=None,
                command_id=str(source.command) if source.command else None,
            )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching status for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching source status")


@router.put("/sources/{source_id}", response_model=SourceResponse)
async def update_source(source_id: str, source_update: SourceUpdate):
    """Update a source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Update only provided fields
        if source_update.title is not None:
            source.title = source_update.title
        if source_update.topics is not None:
            source.topics = source_update.topics

        await source.save()

        embedded_chunks = await source.get_embedded_chunks()
        return _source_to_response(source, embedded_chunks=embedded_chunks)
    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating source")


@router.post("/sources/{source_id}/retry", response_model=SourceResponse)
async def retry_source_processing(source_id: str):
    """Retry processing for a failed or stuck source."""
    try:
        # First, verify source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Check if source already has a running command
        if source.command:
            try:
                status = await source.get_status()
                if status in ["running", "queued"]:
                    raise HTTPException(
                        status_code=400,
                        detail="Source is already processing. Cannot retry while processing is active.",
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to check current status for source {source_id}: {e}"
                )
                # Continue with retry if we can't check status

        # Get notebooks that this source belongs to. `reference` is a graph edge
        # (RELATE source->reference->notebook), so it only has `in`/`out` columns —
        # there is no `source`/`notebook` column. Mirror the working query at the
        # source-list path above. See issue #861.
        references = await repo_relations("reference", source=source.id or source_id)
        notebook_ids = [str(relation["out"]) for relation in references]

        if not notebook_ids:
            raise HTTPException(
                status_code=400, detail="Source is not associated with any notebooks"
            )

        # Prepare content_state based on source asset
        content_state = {}
        if source.asset:
            if source.asset.file_path:
                # Don't re-queue a retry for a file content-core can't extract.
                await _assert_file_supported(source.asset.file_path)
                content_state = {
                    "file_path": source.asset.file_path,
                    "delete_source": False,  # Don't delete on retry
                }
            elif source.asset.url:
                content_state = {"url": source.asset.url}
            else:
                raise HTTPException(
                    status_code=400, detail="Source asset has no file_path or url"
                )
        else:
            # Check if it's a text source by trying to get full_text
            if source.full_text:
                content_state = {"content": source.full_text}
            else:
                raise HTTPException(
                    status_code=400, detail="Cannot determine source content for retry"
                )

        try:
            # Import command modules to ensure they're registered
            import commands.source_commands  # noqa: F401

            # Submit new command for background processing
            command_input = SourceProcessingInput(
                source_id=str(source.id),
                content_state=content_state,
                notebook_ids=notebook_ids,
                transformations=[],  # Use default transformations on retry
                embed=True,  # Always embed on retry
            )

            command_id = await CommandService.submit_command_job(
                "open_notebook",  # app name
                "process_source",  # command name
                command_input.model_dump(),
            )

            logger.info(
                f"Submitted retry processing command: {command_id} for source {source_id}"
            )

            # Update source with new command ID
            # command_id already includes 'command:' prefix
            source.command = ensure_record_id(command_id)
            await source.save()

            # Get current embedded chunks count
            embedded_chunks = await source.get_embedded_chunks()

            # Return updated source response
            return _source_to_response(
                source,
                embedded_chunks=embedded_chunks,
                command_id=command_id,
                status="queued",
                processing_info={"retry": True, "queued": True},
            )

        except Exception as e:
            logger.error(
                f"Failed to submit retry processing command for source {source_id}: {e}"
            )
            raise HTTPException(
                status_code=500, detail="Failed to queue retry processing"
            )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error retrying source processing for {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrying source processing")


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    """Delete a source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        await source.delete()

        return {"message": "Source deleted successfully"}
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting source")


@router.get("/sources/{source_id}/insights", response_model=List[SourceInsightResponse])
async def get_source_insights(source_id: str):
    """Get all insights for a specific source."""
    try:
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        insights = await source.get_insights()
        return [
            SourceInsightResponse(
                id=insight.id or "",
                source_id=source_id,
                insight_type=insight.insight_type,
                content=insight.content,
                created=insight.created.isoformat() if insight.created else None,
                updated=insight.updated.isoformat() if insight.updated else None,
            )
            for insight in insights
        ]
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching insights for source {source_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching insights")


@router.post(
    "/sources/{source_id}/insights",
    response_model=InsightCreationResponse,
    status_code=202,
)
async def create_source_insight(source_id: str, request: CreateSourceInsightRequest):
    """
    Start insight generation for a source by running a transformation.

    This endpoint returns immediately with a 202 Accepted status.
    The transformation runs asynchronously in the background via the job queue.
    Poll GET /sources/{source_id}/insights to see when the insight is ready.
    """
    try:
        # Validate source exists
        source = await Source.get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")

        # Validate transformation exists
        transformation = await Transformation.get(request.transformation_id)
        if not transformation:
            raise HTTPException(status_code=404, detail="Transformation not found")

        # Submit transformation as background job (fire-and-forget)
        command_id = submit_command(
            "open_notebook",
            "run_transformation",
            {
                "source_id": source_id,
                "transformation_id": request.transformation_id,
            },
        )
        logger.info(
            f"Submitted run_transformation command {command_id} for source {source_id}"
        )

        # Return immediately with command_id for status tracking
        return InsightCreationResponse(
            status="pending",
            message="Insight generation started",
            source_id=source_id,
            transformation_id=request.transformation_id,
            command_id=str(command_id),
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error starting insight generation for source {source_id}: {e}")
        raise HTTPException(status_code=500, detail="Error starting insight generation")
