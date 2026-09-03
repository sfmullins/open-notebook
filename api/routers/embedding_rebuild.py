from fastapi import APIRouter, HTTPException
from loguru import logger

from api.command_service import CommandService
from api.models import (
    RebuildProgress,
    RebuildRequest,
    RebuildResponse,
    RebuildStats,
    RebuildStatusResponse,
)
from command_queue import get_command_status
from open_notebook.database.embeddings import (
    count_distinct_source_embeddings,
    count_record_embeddings,
)
from open_notebook.database.repository import repo_count
from open_notebook.exceptions import OpenNotebookError

router = APIRouter()


@router.post("/rebuild", response_model=RebuildResponse)
async def start_rebuild(request: RebuildRequest):
    """
    Start a background job to rebuild embeddings.

    - **mode**: "existing" (re-embed items with embeddings) or "all" (embed everything)
    - **include_sources**: Include sources in rebuild (default: true)
    - **include_notes**: Include notes in rebuild (default: true)
    - **include_insights**: Include insights in rebuild (default: true)

    Returns command ID to track progress and estimated item count.
    """
    try:
        logger.info(f"Starting rebuild request: mode={request.mode}")

        # Import commands to ensure they're registered
        import commands.embedding_commands  # noqa: F401

        # Estimate total items (quick count query)
        # This is a rough estimate before the command runs
        total_estimate = 0

        if request.include_sources:
            if request.mode == "existing":
                total_estimate += await count_distinct_source_embeddings()
            else:
                total_estimate += await repo_count(
                    "source",
                    non_null_fields={"full_text"},
                    non_empty_fields={"full_text"},
                )

        if request.include_notes:
            if request.mode == "existing":
                total_estimate += await count_record_embeddings("note")
            else:
                total_estimate += await repo_count(
                    "note", non_null_fields={"content"}, non_empty_fields={"content"}
                )

        if request.include_insights:
            if request.mode == "existing":
                total_estimate += await count_record_embeddings("source_insight")
            else:
                total_estimate += await repo_count("source_insight")

        logger.info(f"Estimated {total_estimate} items to process")

        # Submit command
        command_id = await CommandService.submit_command_job(
            "open_notebook",
            "rebuild_embeddings",
            {
                "mode": request.mode,
                "include_sources": request.include_sources,
                "include_notes": request.include_notes,
                "include_insights": request.include_insights,
            },
        )

        logger.info(f"Submitted rebuild command: {command_id}")

        return RebuildResponse(
            command_id=command_id,
            total_items=total_estimate,
            message=f"Rebuild operation started. Estimated {total_estimate} items to process.",
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Failed to start rebuild: {e}")
        logger.exception(e)
        raise HTTPException(
            status_code=500, detail=f"Failed to start rebuild operation: {str(e)}"
        )


@router.get("/rebuild/{command_id}/status", response_model=RebuildStatusResponse)
async def get_rebuild_status(command_id: str):
    """
    Get the status of a rebuild operation.

    Returns:
    - **status**: queued, running, completed, failed
    - **progress**: processed count, total count, percentage
    - **stats**: breakdown by type (sources, notes, insights, failed)
    - **timestamps**: started_at, completed_at
    """
    try:
        # Get command status from command_queue
        status = await get_command_status(command_id)

        if not status:
            raise HTTPException(status_code=404, detail="Rebuild command not found")

        # Build response based on status
        response = RebuildStatusResponse(
            command_id=command_id,
            status=status.status,
        )

        # Extract metadata from command result
        if status.result and isinstance(status.result, dict):
            result = status.result

            # Build progress info
            if "total_items" in result and "jobs_submitted" in result:
                total = result["total_items"]
                submitted = result["jobs_submitted"]
                response.progress = RebuildProgress(
                    processed=submitted,
                    total=total,
                    percentage=round((submitted / total * 100) if total > 0 else 0, 2),
                )

            # Build stats
            response.stats = RebuildStats(
                sources=result.get("sources_submitted", 0),
                notes=result.get("notes_submitted", 0),
                insights=result.get("insights_submitted", 0),
                failed=result.get("failed_submissions", 0),
            )

        # Add timestamps
        if hasattr(status, "created") and status.created:
            response.started_at = str(status.created)
        if hasattr(status, "updated") and status.updated:
            response.completed_at = str(status.updated)

        # Add error message if failed
        if (
            status.status == "failed"
            and status.result
            and isinstance(status.result, dict)
        ):
            response.error_message = status.result.get("error_message", "Unknown error")

        return response

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Failed to get rebuild status: {e}")
        logger.exception(e)
        raise HTTPException(
            status_code=500, detail=f"Failed to get rebuild status: {str(e)}"
        )
