#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(rel): return (ROOT / rel).read_text(encoding='utf-8')
def write(rel, text): (ROOT / rel).write_text(text, encoding='utf-8')
def one(text, old, new, label):
    n=text.count(old)
    if n!=1: raise RuntimeError(f'{label}: expected 1 match, got {n}')
    return text.replace(old,new,1)
def block(text,start,end,new,label):
    s=text.find(start)
    if s<0: raise RuntimeError(f'{label}: start missing')
    e=text.find(end,s)
    if e<0: raise RuntimeError(f'{label}: end missing')
    return text[:s]+new+text[e:]

# Embedding repository helpers.
p='open_notebook/database/embeddings.py'; t=read(p)
marker='async def text_search_pg(\n'
helpers='''async def source_ids_with_embeddings() -> list[str]:
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


'''
if helpers.strip() not in t:
    idx=t.find(marker)
    if idx<0: raise RuntimeError('embedding helper insertion marker missing')
    t=t[:idx]+helpers+t[idx:]
write(p,t)

# Embedding command worker.
p='commands/embedding_commands.py'; t=read(p)
t=one(t,'from open_notebook.database.repository import ensure_record_id, repo_insert, repo_query\n',
'''from open_notebook.database.embeddings import (
    delete_source_embeddings,
    record_ids_with_embeddings,
    source_ids_with_embeddings,
    upsert_record_embedding,
)
from open_notebook.database.repository import ensure_record_id, repo_create, repo_insert, repo_list
''','embedding command imports')
t=one(t,'''    # 3. UPSERT embedding into the record
    await repo_query(
        "UPDATE $record_id SET embedding = $embedding",
        {
            "record_id": ensure_record_id(record_id),
            "embedding": embedding,
        },
    )
''','''    # 3. Store the embedding in the dedicated pgvector table.
    await upsert_record_embedding(
        ensure_record_id(record_id), record.content, embedding
    )
''','markdown embedding write')
t=one(t,'''        await repo_query(
            "DELETE source_embedding WHERE source = $source_id",
            {"source_id": ensure_record_id(input_data.source_id)},
        )
''','''        await delete_source_embeddings(ensure_record_id(input_data.source_id))
''','source embedding delete')
start='''        # 1. Create insight record in database
        result = await repo_query(
'''
s=t.find(start)
if s<0: raise RuntimeError('create insight start missing')
e=t.find('        # 2. Submit embedding command',s)
if e<0: raise RuntimeError('create insight end missing')
t=t[:s]+'''        # 1. Create insight record in database
        result = await repo_create(
            "source_insight",
            {
                "source": input_data.source_id,
                "insight_type": input_data.insight_type,
                "content": input_data.content,
            },
        )
        insight_id = str(result.get("id", ""))
        if not insight_id:
            raise ValueError("Failed to create insight - no ID in result")

'''+t[e:]
t=t.replace('SurrealDB transaction conflicts','database transaction conflicts')
# Rebuild item collection function: replace query snippets individually.
old='''            result = await repo_query(
                """
                RETURN array::distinct(
                    SELECT VALUE source.id
                    FROM source_embedding
                    WHERE embedding != none AND array::len(embedding) > 0
                )
                """
            )
            if result:
                items["sources"] = [str(item) for item in result]
            else:
                items["sources"] = []'''
new='''            items["sources"] = await source_ids_with_embeddings()'''
t=one(t,old,new,'rebuild existing sources')
old='''            result = await repo_query(
                "SELECT id FROM source WHERE full_text != none AND string::trim(full_text) != ''"
            )
            items["sources"] = [str(item["id"]) for item in result] if result else []'''
new='''            result = await repo_list(
                "source", non_null_fields={"full_text"}, non_empty_fields={"full_text"}
            )
            items["sources"] = [str(item["id"]) for item in result]'''
t=one(t,old,new,'rebuild all sources')
old='''            result = await repo_query(
                "SELECT id FROM note WHERE embedding != none AND array::len(embedding) > 0"
            )'''
t=one(t,old,'            result = [{"id": rid} for rid in await record_ids_with_embeddings("note")]','rebuild existing notes')
old='''            result = await repo_query(
                "SELECT id FROM note WHERE content != none AND string::trim(content) != ''"
            )'''
t=one(t,old,'            result = await repo_list("note", non_null_fields={"content"}, non_empty_fields={"content"})','rebuild all notes')
old='''            result = await repo_query(
                "SELECT id FROM source_insight WHERE embedding != none AND array::len(embedding) > 0"
            )'''
t=one(t,old,'            result = [{"id": rid} for rid in await record_ids_with_embeddings("source_insight")]','rebuild existing insights')
old='''            result = await repo_query(
                "SELECT id FROM source_insight WHERE content != none AND string::trim(content) != ''"
            )'''
t=one(t,old,'            result = await repo_list("source_insight", non_null_fields={"content"}, non_empty_fields={"content"})','rebuild all insights')
write(p,t)

# Embedding rebuild API counts.
p='api/routers/embedding_rebuild.py'; t=read(p)
t=one(t,'from open_notebook.database.repository import repo_query\n',
'''from open_notebook.database.embeddings import (
    count_distinct_source_embeddings,
    count_record_embeddings,
)
from open_notebook.database.repository import repo_count
''','rebuild imports')
start='''        if request.include_sources:
'''; end='''        logger.info(f"Estimated {total_estimate} items to process")
'''
new='''        if request.include_sources:
            if request.mode == "existing":
                total_estimate += await count_distinct_source_embeddings()
            else:
                total_estimate += await repo_count(
                    "source", non_null_fields={"full_text"}, non_empty_fields={"full_text"}
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

'''
t=block(t,start,end,new,'rebuild count block')
write(p,t)

# Shared chat relation verification.
p='api/routers/_chat_shared.py'; t=read(p)
t=one(t,'from open_notebook.database.repository import ensure_record_id, repo_query\n','from open_notebook.database.repository import repo_relation_exists\n','chat shared import')
start='''    relation_query = await repo_query(
'''; end='''    if not relation_query:
'''
new='''    relation_query = await repo_relation_exists(
        "refers_to", source=full_session_id, target=full_source_id
    )
'''
t=block(t,start,end,new,'chat shared relation')
write(p,t)

# Chat router: session -> notebook relations.
p='api/routers/chat.py'; t=read(p)
t=one(t,'from open_notebook.database.repository import ensure_record_id, repo_query\n','from open_notebook.database.repository import repo_relations\n','chat import')
for label in ('get','update','execute'):
    old='''        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
'''
    if old not in t: raise RuntimeError(f'chat {label} relation missing')
    t=t.replace(old,'        notebook_query = await repo_relations("refers_to", source=full_session_id)\n',1)
# relation rows now expose "out" identically.
write(p,t)

# Source-chat router: related session records directly.
p='api/routers/source_chat.py'; t=read(p)
t=one(t,'from open_notebook.database.repository import ensure_record_id, repo_query\n','from open_notebook.database.repository import repo_related_records\n','source chat import')
start='''        # Get sessions that refer to this source - first get relations, then sessions
'''; end='''        # Sort sessions by created date (newest first)
'''
new='''        # Resolve sessions linked to this source in one repository operation.
        session_rows = await repo_related_records(
            "refers_to", target=full_source_id, related_side="source"
        )
        sessions = []
        for session_data in session_rows:
            session_id = str(session_data.get("id", ""))
            msg_count = await get_session_message_count(source_chat_graph, session_id)
            sessions.append(
                SourceChatSessionResponse(
                    id=session_id,
                    title=session_data.get("title") or "Untitled Session",
                    source_id=source_id,
                    model_override=session_data.get("model_override"),
                    created=str(session_data.get("created")),
                    updated=str(session_data.get("updated")),
                    message_count=msg_count,
                )
            )

'''
t=block(t,start,end,new,'source chat sessions')
write(p,t)

# Notebook API: typed records + relation counts.
p='api/routers/notebooks.py'; t=read(p)
t=one(t,'from open_notebook.database.repository import ensure_record_id, repo_query\n',
'''from open_notebook.database.repository import (
    repo_delete_relations,
    repo_list,
    repo_relate,
    repo_relation_count,
    repo_relation_exists,
    repo_update_record,
)
''','notebooks imports')
t=block(t,'async def _stamp_notebook_view(notebook_id: str) -> None:\n','\n\ndef _recently_viewed_notebook',
'''async def _stamp_notebook_view(notebook_id: str) -> None:
    try:
        from datetime import datetime, timezone
        await repo_update_record(notebook_id, {"last_viewed_at": datetime.now(timezone.utc)})
    except Exception as e:
        logger.warning(f"Failed to stamp last_viewed_at for notebook {notebook_id}: {e}")
''','stamp notebook')
t=block(t,'@router.get("/notebooks", response_model=List[NotebookResponse])\n','@router.post("/notebooks", response_model=NotebookResponse)\n',
'''@router.get("/notebooks", response_model=List[NotebookResponse])
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


''','get notebooks')
t=block(t,'@router.get("/recently-viewed", response_model=List[RecentlyViewedResponse])\n','@router.get(\n    "/notebooks/{notebook_id}/delete-preview", response_model=NotebookDeletePreview\n)\n',
'''@router.get("/recently-viewed", response_model=List[RecentlyViewedResponse])
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


''','recently viewed')
t=block(t,'@router.get("/notebooks/{notebook_id}", response_model=NotebookResponse)\n','@router.put("/notebooks/{notebook_id}", response_model=NotebookResponse)\n',
'''@router.get("/notebooks/{notebook_id}", response_model=NotebookResponse)
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


''','get notebook')
t=block(t,'@router.put("/notebooks/{notebook_id}", response_model=NotebookResponse)\n','@router.post("/notebooks/{notebook_id}/sources/{source_id}")\n',
'''@router.put("/notebooks/{notebook_id}", response_model=NotebookResponse)
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


''','update notebook')
t=block(t,'@router.post("/notebooks/{notebook_id}/sources/{source_id}")\n','@router.delete("/notebooks/{notebook_id}/sources/{source_id}")\n',
'''@router.post("/notebooks/{notebook_id}/sources/{source_id}")
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


''','add source')
t=block(t,'@router.delete("/notebooks/{notebook_id}/sources/{source_id}")\n','@router.delete("/notebooks/{notebook_id}", response_model=NotebookDeleteResponse)\n',
'''@router.delete("/notebooks/{notebook_id}/sources/{source_id}")
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


''','remove source')
write(p,t)

for rel in ('scripts/pr2_query_batch3.py','.github/workflows/pr2-query-batch3.yml'):
    x=ROOT/rel
    if x.exists(): x.unlink()
