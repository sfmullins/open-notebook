from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from api.models import (
    NotebookCreate,
    NotebookDeletePreview,
    NotebookDeleteResponse,
    NotebookResponse,
    NotebookUpdate,
    RecentlyViewedResponse,
)
from open_notebook.database.repository import (
    repo_delete_relations,
    repo_list,
    repo_relate,
    repo_relation_count,
    repo_relation_exists,
    repo_update_record,
)
from open_notebook.domain.notebook import Notebook, Source
from open_notebook.exceptions import (
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)

router = APIRouter()


def _last_viewed_sort_key(item: RecentlyViewedResponse) -> str:
    return item.last_viewed_at


async def _stamp_notebook_view(notebook_id: str) -> None:
    try:
        from datetime import datetime, timezone
        await repo_update_record(notebook_id, {"last_viewed_at": datetime.now(timezone.utc)})
    except Exception as e:
        logger.warning(f"Failed to stamp last_viewed_at for notebook {notebook_id}: {e}")


def _recently_viewed_notebook(row: dict) -> RecentlyViewedResponse:
    return RecentlyViewedResponse(
        type="notebook",
        id=str(row.get("id", "")),
        title=row.get("title") or row.get("name") or "Untitled notebook",
        last_viewed_at=str(row.get("last_viewed_at", "")),
    )


def _recently_viewed_source(row: dict) -> RecentlyViewedResponse:
    return RecentlyViewedResponse(
        type="source",
        id=str(row.get("id", "")),
        title=row.get("title") or "Untitled source",
        last_viewed_at=str(row.get("last_viewed_at", "")),
    )


@router.get("/notebooks", response_model=List[NotebookResponse])
async def get_notebooks(
    archived: Optional[bool] = Query(None, description="Filter by archived status"),
    order_by: str = Query("updated desc", description="Order by field and direction"),
):
    try:
        allowed_fields = {"name", "created", "updated"}
        parts = order_by.strip().lower().split()
        if not (1 <= len(parts) <= 2) or parts[0] not in allowed_fields or (len(parts) == 2 and parts[1] not in {"asc", "desc"}):
            raise HTTPException(status_code=400, detail="Invalid order_by")
        field = parts[0]
        descending = len(parts) == 2 and parts[1] == "desc"
        filters = {"archived": archived} if archived is not None else None
        rows = await repo_list("notebook", filters=filters, order_by=field, descending=descending)
        responses = []
        for nb in rows:
            nb_id = str(nb.get("id", ""))
            responses.append(NotebookResponse(
                id=nb_id,
                name=nb.get("name", ""), description=nb.get("description", ""),
                archived=nb.get("archived", False), created=str(nb.get("created", "")),
                updated=str(nb.get("updated", "")),
                source_count=await repo_relation_count("reference", target=nb_id),
                note_count=await repo_relation_count("artifact", target=nb_id),
            ))
        return responses
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching notebooks: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching notebooks: {str(e)}")


@router.post("/notebooks", response_model=NotebookResponse)
async def create_notebook(notebook: NotebookCreate):
    """Create a new notebook."""
    try:
        new_notebook = Notebook(
            name=notebook.name,
            description=notebook.description,
        )
        await new_notebook.save()

        return NotebookResponse(
            id=new_notebook.id or "",
            name=new_notebook.name,
            description=new_notebook.description,
            archived=new_notebook.archived or False,
            created=str(new_notebook.created),
            updated=str(new_notebook.updated),
            source_count=0,  # New notebook has no sources
            note_count=0,  # New notebook has no notes
        )
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating notebook: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating notebook: {str(e)}"
        )


@router.get("/recently-viewed", response_model=List[RecentlyViewedResponse])
async def get_recently_viewed(
    limit: int = Query(12, ge=1, le=50, description="Number of items to return"),
):
    try:
        notebooks = await repo_list("notebook", non_null_fields={"last_viewed_at"}, order_by="last_viewed_at", descending=True, limit=limit)
        sources = await repo_list("source", non_null_fields={"last_viewed_at"}, order_by="last_viewed_at", descending=True, limit=limit)
        items = [*[_recently_viewed_notebook(nb) for nb in notebooks], *[_recently_viewed_source(src) for src in sources]]
        items.sort(key=_last_viewed_sort_key, reverse=True)
        return items[:limit]
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.exception(f"Error fetching recently viewed items: {e}")
        raise HTTPException(status_code=500, detail="Error fetching recently viewed items")


@router.get(
    "/notebooks/{notebook_id}/delete-preview", response_model=NotebookDeletePreview
)
async def get_notebook_delete_preview(notebook_id: str):
    """Get a preview of what will be deleted when this notebook is deleted."""
    try:
        notebook = await Notebook.get(notebook_id)

        preview = await notebook.get_delete_preview()

        return NotebookDeletePreview(
            notebook_id=str(notebook.id),
            notebook_name=notebook.name,
            note_count=preview["note_count"],
            exclusive_source_count=preview["exclusive_source_count"],
            shared_source_count=preview["shared_source_count"],
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error getting delete preview for notebook {notebook_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching notebook deletion preview: {str(e)}",
        )


@router.get("/notebooks/{notebook_id}", response_model=NotebookResponse)
async def get_notebook(notebook_id: str):
    try:
        notebook = await Notebook.get(notebook_id)
        await _stamp_notebook_view(notebook.id or notebook_id)
        nb_id = notebook.id or notebook_id
        return NotebookResponse(
            id=nb_id, name=notebook.name, description=notebook.description,
            archived=notebook.archived or False, created=str(notebook.created), updated=str(notebook.updated),
            source_count=await repo_relation_count("reference", target=nb_id),
            note_count=await repo_relation_count("artifact", target=nb_id),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching notebook {notebook_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching notebook: {str(e)}")


@router.put("/notebooks/{notebook_id}", response_model=NotebookResponse)
async def update_notebook(notebook_id: str, notebook_update: NotebookUpdate):
    try:
        notebook = await Notebook.get(notebook_id)
        if notebook_update.name is not None: notebook.name = notebook_update.name
        if notebook_update.description is not None: notebook.description = notebook_update.description
        if notebook_update.archived is not None: notebook.archived = notebook_update.archived
        await notebook.save()
        nb_id = notebook.id or notebook_id
        return NotebookResponse(
            id=nb_id, name=notebook.name, description=notebook.description,
            archived=notebook.archived or False, created=str(notebook.created), updated=str(notebook.updated),
            source_count=await repo_relation_count("reference", target=nb_id),
            note_count=await repo_relation_count("artifact", target=nb_id),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating notebook {notebook_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating notebook: {str(e)}")


@router.post("/notebooks/{notebook_id}/sources/{source_id}")
async def add_source_to_notebook(notebook_id: str, source_id: str):
    try:
        await Notebook.get(notebook_id)
        await Source.get(source_id)
        if not await repo_relation_exists("reference", source=source_id, target=notebook_id):
            await repo_relate(source_id, "reference", notebook_id)
        return {"message": "Source linked to notebook successfully"}
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook or source not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error linking source {source_id} to notebook {notebook_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error linking source to notebook: {str(e)}")


@router.delete("/notebooks/{notebook_id}/sources/{source_id}")
async def remove_source_from_notebook(notebook_id: str, source_id: str):
    try:
        await Notebook.get(notebook_id)
        await repo_delete_relations("reference", source=source_id, target=notebook_id)
        return {"message": "Source removed from notebook successfully"}
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error removing source {source_id} from notebook {notebook_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error removing source from notebook: {str(e)}")


@router.delete("/notebooks/{notebook_id}", response_model=NotebookDeleteResponse)
async def delete_notebook(
    notebook_id: str,
    delete_exclusive_sources: bool = Query(
        False,
        description="Whether to delete sources that belong only to this notebook",
    ),
):
    """
    Delete a notebook with cascade deletion.

    Always deletes all notes associated with the notebook.
    If delete_exclusive_sources is True, also deletes sources that belong only
    to this notebook (not linked to any other notebooks).
    """
    try:
        notebook = await Notebook.get(notebook_id)

        result = await notebook.delete(
            delete_exclusive_sources=delete_exclusive_sources
        )

        return NotebookDeleteResponse(
            message="Notebook deleted successfully",
            deleted_notes=result["deleted_notes"],
            deleted_sources=result["deleted_sources"],
            unlinked_sources=result["unlinked_sources"],
            deleted_chat_sessions=result["deleted_chat_sessions"],
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting notebook {notebook_id}: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting notebook: {str(e)}"
        )
