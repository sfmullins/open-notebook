#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_block(text: str, start: str, end: str, replacement: str, label: str) -> str:
    s = text.find(start)
    if s < 0:
        raise RuntimeError(f"{label}: start marker not found")
    e = text.find(end, s)
    if e < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:s] + replacement + text[e:]


# ---------------------------------------------------------------------------
# Notebook/domain layer: replace SurrealQL-shaped calls with repository ops.
# ---------------------------------------------------------------------------
path = "open_notebook/domain/notebook.py"
text = read(path)
text = replace_once(
    text,
    "from open_notebook.database.repository import ensure_record_id, repo_query\n",
    "from open_notebook.database.embeddings import (\n"
    "    count_source_embeddings,\n"
    "    delete_source_embeddings,\n"
    "    text_search_pg,\n"
    "    vector_search_pg,\n"
    ")\n"
    "from open_notebook.database.repository import (\n"
    "    ensure_record_id,\n"
    "    repo_delete_relations,\n"
    "    repo_delete_where,\n"
    "    repo_list,\n"
    "    repo_related_records,\n"
    "    repo_relation_count,\n"
    "    repo_relations,\n"
    ")\n",
    "notebook imports",
)

text = replace_block(
    text,
    "    async def get_sources(self, include_full_text: bool = False) -> List[\"Source\"]:\n",
    "    async def get_notes(self, include_content: bool = False) -> List[\"Note\"]:\n",
    '''    async def get_sources(self, include_full_text: bool = False) -> List["Source"]:
        try:
            rows = await repo_related_records(
                "reference",
                target=self.id,
                related_side="source",
                order_by="updated",
                descending=True,
            )
            sources = []
            for row in rows:
                data = dict(row)
                if not include_full_text:
                    data.pop("full_text", None)
                sources.append(Source(**data))
            return sources
        except Exception as e:
            logger.error(f"Error fetching sources for notebook {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

''',
    "Notebook.get_sources",
)
text = replace_block(
    text,
    "    async def get_notes(self, include_content: bool = False) -> List[\"Note\"]:\n",
    "    async def get_context(self) -> str:\n",
    '''    async def get_notes(self, include_content: bool = False) -> List["Note"]:
        try:
            rows = await repo_related_records(
                "artifact",
                target=self.id,
                related_side="source",
                order_by="updated",
                descending=True,
            )
            notes = []
            for row in rows:
                data = dict(row)
                data.pop("embedding", None)
                if not include_content:
                    data.pop("content", None)
                notes.append(Note(**data))
            return notes
        except Exception as e:
            logger.error(f"Error fetching notes for notebook {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

''',
    "Notebook.get_notes",
)
text = replace_block(
    text,
    "    async def get_chat_sessions(self) -> List[\"ChatSession\"]:\n",
    "    async def get_delete_preview(self) -> Dict[str, Any]:\n",
    '''    async def get_chat_sessions(self) -> List["ChatSession"]:
        try:
            rows = await repo_related_records(
                "refers_to",
                target=self.id,
                related_side="source",
                order_by="updated",
                descending=True,
            )
            return [ChatSession(**row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching chat sessions for notebook {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

''',
    "Notebook.get_chat_sessions",
)
text = replace_block(
    text,
    "    async def get_delete_preview(self) -> Dict[str, Any]:\n",
    "    async def delete(self, delete_exclusive_sources: bool = False) -> Dict[str, int]:\n",
    '''    async def get_delete_preview(self) -> Dict[str, Any]:
        """Return the records affected by deleting this notebook."""
        try:
            note_count = await repo_relation_count("artifact", target=self.id)
            references = await repo_relations("reference", target=self.id)
            exclusive_count = 0
            shared_count = 0
            for relation in references:
                source_id = relation["in"]
                assigned = await repo_relation_count("reference", source=source_id)
                if assigned <= 1:
                    exclusive_count += 1
                else:
                    shared_count += 1
            return {
                "note_count": note_count,
                "exclusive_source_count": exclusive_count,
                "shared_source_count": shared_count,
            }
        except Exception as e:
            logger.error(f"Error getting delete preview for notebook {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(e)

''',
    "Notebook.get_delete_preview",
)
text = replace_block(
    text,
    "    async def delete(self, delete_exclusive_sources: bool = False) -> Dict[str, int]:\n",
    "\n\nclass Asset(BaseModel):\n",
    '''    async def delete(self, delete_exclusive_sources: bool = False) -> Dict[str, int]:
        if self.id is None:
            raise InvalidInputError("Cannot delete notebook without an ID")
        try:
            deleted_notes = 0
            deleted_sources = 0
            unlinked_sources = 0
            deleted_chat_sessions = 0

            for note in await self.get_notes():
                await note.delete()
                deleted_notes += 1
            await repo_delete_relations("artifact", target=self.id)

            references = await repo_relations("reference", target=self.id)
            if delete_exclusive_sources:
                for relation in references:
                    source_id = relation["in"]
                    assigned = await repo_relation_count("reference", source=source_id)
                    if assigned <= 1:
                        try:
                            await (await Source.get(source_id)).delete()
                            deleted_sources += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete exclusive source {source_id}: {e}")
                    else:
                        unlinked_sources += 1
            else:
                unlinked_sources = len(references)
            await repo_delete_relations("reference", target=self.id)

            for chat_session in await self.get_chat_sessions():
                await chat_session.delete()
                deleted_chat_sessions += 1
            await super().delete()
            return {
                "deleted_notes": deleted_notes,
                "deleted_sources": deleted_sources,
                "unlinked_sources": unlinked_sources,
                "deleted_chat_sessions": deleted_chat_sessions,
            }
        except Exception as e:
            logger.error(f"Error deleting notebook {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(f"Failed to delete notebook: {e}")
''',
    "Notebook.delete",
)

text = replace_block(
    text,
    "class SourceEmbedding(ObjectModel):\n",
    "class SourceInsight(ObjectModel):\n",
    '''class SourceEmbedding(ObjectModel):
    table_name: ClassVar[str] = "source_embedding"
    content: str
    source: Optional[str] = None

    async def get_source(self) -> "Source":
        if not self.source:
            raise DatabaseOperationError(f"Embedding {self.id} has no source reference")
        return await Source.get(str(self.source))


''',
    "SourceEmbedding",
)
text = replace_block(
    text,
    "class SourceInsight(ObjectModel):\n",
    "class Source(ObjectModel):\n",
    '''class SourceInsight(ObjectModel):
    table_name: ClassVar[str] = "source_insight"
    insight_type: str
    content: str
    source: Optional[str] = None

    @classmethod
    async def get_for_sources(
        cls, source_ids: List[str]
    ) -> Dict[str, List["SourceInsight"]]:
        grouped: Dict[str, List[SourceInsight]] = {sid: [] for sid in source_ids if sid}
        if not grouped:
            return grouped
        try:
            rows = await repo_list("source_insight", in_filters={"source": grouped.keys()})
        except Exception as e:
            logger.error(f"Error batch-fetching insights for sources: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError("Failed to fetch insights for sources")
        for row in rows:
            key = str(row.get("source"))
            grouped.setdefault(key, []).append(cls(**row))
        return grouped

    async def get_source(self) -> "Source":
        if not self.source:
            raise DatabaseOperationError(f"Insight {self.id} has no source reference")
        return await Source.get(str(self.source))

    async def save_as_note(self, notebook_id: Optional[str] = None) -> Any:
        source = await self.get_source()
        note = Note(
            title=f"{self.insight_type} from source {source.title}",
            content=self.content,
        )
        await note.save()
        if notebook_id:
            await note.add_to_notebook(notebook_id)
        return note


''',
    "SourceInsight",
)
text = replace_block(
    text,
    "    async def get_embedded_chunks(self) -> int:\n",
    "    async def add_to_notebook(self, notebook_id: str) -> Any:\n",
    '''    async def get_embedded_chunks(self) -> int:
        try:
            return await count_source_embeddings(ensure_record_id(self.id))
        except Exception as e:
            logger.error(f"Error fetching chunks count for source {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(f"Failed to count chunks for source: {str(e)}")

    async def get_insights(self) -> List[SourceInsight]:
        try:
            rows = await repo_list("source_insight", filters={"source": self.id})
            return [SourceInsight(**row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching insights for source {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError("Failed to fetch insights for source")

''',
    "Source embedding/insight helpers",
)
# Replace only the cleanup query block inside Source.delete.
old_cleanup = '''            source_id = ensure_record_id(self.id)
            await repo_query(
                "DELETE source_embedding WHERE source = $source_id",
                {"source_id": source_id},
            )
            await repo_query(
                "DELETE source_insight WHERE source = $source_id",
                {"source_id": source_id},
            )
            logger.debug(f"Deleted embeddings and insights for source {self.id}")'''
new_cleanup = '''            source_id = ensure_record_id(self.id)
            await delete_source_embeddings(source_id)
            await repo_delete_where("source_insight", filters={"source": self.id})
            logger.debug(f"Deleted embeddings and insights for source {self.id}")'''
text = replace_once(text, old_cleanup, new_cleanup, "Source.delete cleanup")

text = replace_block(
    text,
    "async def text_search(\n",
    "",
    '''async def text_search(
    keyword: str, results: int, source: bool = True, note: bool = True
):
    if not keyword:
        raise InvalidInputError("Search keyword cannot be empty")
    try:
        return await text_search_pg(keyword, results, source, note)
    except Exception as e:
        logger.error(f"Error performing text search: {str(e)}")
        logger.exception(e)
        raise DatabaseOperationError(e)


async def vector_search(
    keyword: str,
    results: int,
    source: bool = True,
    note: bool = True,
    minimum_score=0.2,
):
    if not keyword:
        raise InvalidInputError("Search keyword cannot be empty")
    try:
        from open_notebook.utils.embedding import generate_embedding

        embed = await generate_embedding(keyword)
        return await vector_search_pg(
            embed,
            results,
            source=source,
            note=note,
            minimum_score=minimum_score,
        )
    except Exception as e:
        logger.error(f"Error performing vector search: {str(e)}")
        logger.exception(e)
        raise DatabaseOperationError(e)
''',
    "search functions",
)
write(path, text)

# ---------------------------------------------------------------------------
# Model discovery: structured provider filtering/counting.
# ---------------------------------------------------------------------------
path = "open_notebook/ai/model_discovery.py"
text = read(path)
text = replace_once(
    text,
    "from open_notebook.database.repository import repo_query\n",
    "from open_notebook.database.repository import repo_list\n",
    "model_discovery import",
)
text = re.sub(
    r'''existing_models = await repo_query\(\n\s*"SELECT string::lowercase\(name\) as name, string::lowercase\(type\) as type FROM model "\n\s*"WHERE string::lowercase\(provider\) = \$provider",\n\s*\{"provider": provider\.lower\(\)\},\n\s*\)''',
    '''existing_models = await repo_list(\n            "model",\n            filters={"provider": provider},\n            case_insensitive_fields={"provider"},\n        )''',
    text,
    count=1,
)
old = '''result = await repo_query(
        "SELECT type, count() as count FROM model WHERE string::lowercase(provider) = string::lowercase($provider) GROUP BY type",
        {"provider": provider},
    )

    counts = {'''
if old not in text:
    raise RuntimeError("model count query snippet not found")
text = text.replace(
    old,
    '''rows = await repo_list(
        "model",
        filters={"provider": provider},
        case_insensitive_fields={"provider"},
    )
    result = []
    grouped: Dict[str, int] = {}
    for row in rows:
        model_type = row.get("type")
        if model_type:
            grouped[model_type] = grouped.get(model_type, 0) + 1
    result = [{"type": key, "count": value} for key, value in grouped.items()]

    counts = {''',
    1,
)
write(path, text)

# ---------------------------------------------------------------------------
# HTTP proxy helper: PostgreSQL does not use HTTP proxying; remove Surreal env.
# ---------------------------------------------------------------------------
path = "open_notebook/utils/proxy.py"
write(
    path,
    '''"""HTTP proxy bypass helpers for local Open Notebook services."""

import os

# Local service endpoints should never be sent through a configured external
# HTTP proxy. PostgreSQL itself is not HTTP-based and therefore needs no proxy
# exception derived from DATABASE_URL.
INTERNAL_NO_PROXY_HOSTS = (
    "host.docker.internal",
    "localhost",
    "127.0.0.1",
)
_NO_PROXY_ENV_VARS = ("no_proxy", "NO_PROXY")


def _split_hosts(value: str) -> list[str]:
    return [host.strip() for host in value.split(",") if host.strip()]


def _internal_hosts() -> list[str]:
    return list(INTERNAL_NO_PROXY_HOSTS)


def ensure_internal_no_proxy() -> None:
    """Merge local-service hosts into no_proxy/NO_PROXY without clobbering users."""
    existing: list[str] = []
    seen: set[str] = set()
    for var in _NO_PROXY_ENV_VARS:
        for host in _split_hosts(os.environ.get(var, "")):
            if host == "*":
                return
            key = host.lower()
            if key not in seen:
                seen.add(key)
                existing.append(host)

    merged = list(existing)
    for host in _internal_hosts():
        if host.lower() not in seen:
            seen.add(host.lower())
            merged.append(host)

    combined = ",".join(merged)
    for var in _NO_PROXY_ENV_VARS:
        os.environ[var] = combined
''',
)

# Remove this helper/workflow from the resulting commit.
for rel in (
    "scripts/pr2_query_batch1.py",
    ".github/workflows/pr2-query-batch1.yml",
):
    target = ROOT / rel
    if target.exists():
        target.unlink()
