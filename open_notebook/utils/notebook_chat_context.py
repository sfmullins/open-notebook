"""Model-budgeted context preparation for notebook chat.

The notebook context endpoint intentionally preserves the user's inclusion
selection and reports its full size.  Immediately before inference, this module
compacts an oversized selection to question-relevant source chunks when the
selected model exposes a trustworthy context-window limit.

This avoids silently sending a 50K+ token document to a 40K local model while
preserving the existing full-context behaviour for models whose limits are not
known to Open Notebook.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict

from loguru import logger

from open_notebook.database.chat_retrieval import retrieve_source_chunks_pg
from open_notebook.utils import chunk_text, token_count
from open_notebook.utils.embedding import generate_embedding

CHAT_MAX_RETRIEVED_CHUNKS = 48
CHAT_MIN_CONTEXT_BUDGET = 1024
_RETRIEVAL_HEADER = (
    "[Question-relevant excerpts retrieved from this source because the full "
    "selected context exceeds the active model's context window.]"
)
_FALLBACK_HEADER = (
    "[Locally selected excerpts from this source; semantic retrieval was "
    "unavailable for this request.]"
)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "with",
    "you",
}


@dataclass(frozen=True)
class PreparedNotebookContext:
    context: Dict[str, Any]
    original_tokens: int
    prepared_tokens: int
    mode: str


def _query_terms(query: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[\w-]+", query.lower(), flags=re.UNICODE)
        if len(term) > 1 and term not in _STOPWORDS
    }


def _lexical_chunks(text: str, query: str) -> list[str]:
    """Deterministic no-embedding fallback ranked by literal query overlap."""
    chunks = chunk_text(text)
    if not chunks:
        return []
    terms = _query_terms(query)
    if not terms:
        return chunks

    scored: list[tuple[int, int, str]] = []
    for index, chunk in enumerate(chunks):
        lowered = chunk.lower()
        score = sum(lowered.count(term) for term in terms)
        scored.append((score, -index, chunk))
    scored.sort(reverse=True)

    matches = [chunk for score, _position, chunk in scored if score > 0]
    return matches or chunks


def _strip_large_source_text(context: Dict[str, Any]) -> tuple[Dict[str, Any], dict[str, str]]:
    compact = copy.deepcopy(context)
    originals: dict[str, str] = {}
    sources = compact.get("sources")
    if not isinstance(sources, list):
        return compact, originals

    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "")
        full_text = source.get("full_text")
        if source_id and isinstance(full_text, str) and full_text.strip():
            originals[source_id] = full_text
            source["full_text"] = ""
    return compact, originals


def _trim_note_content_to_fit(context: Dict[str, Any], max_tokens: int) -> None:
    """Bound unusually large notes before source excerpts consume the budget."""
    notes = context.get("notes")
    if not isinstance(notes, list) or token_count(str(context)) <= max_tokens:
        return

    note_dicts = [note for note in notes if isinstance(note, dict) and note.get("content")]
    if not note_dicts:
        return

    per_note_chars = max(256, int((max_tokens * 3) / len(note_dicts)))
    for note in note_dicts:
        content = str(note.get("content") or "")
        if len(content) > per_note_chars:
            note["content"] = content[:per_note_chars] + "\n\n[Note truncated to fit model context.]"
        if token_count(str(context)) <= max_tokens:
            return


def _candidate_with_excerpt(
    context: Dict[str, Any], source_id: str, excerpt: str, *, header: str
) -> Dict[str, Any]:
    candidate = copy.deepcopy(context)
    for source in candidate.get("sources", []):
        if not isinstance(source, dict) or str(source.get("id") or "") != source_id:
            continue
        current = str(source.get("full_text") or "").strip()
        if current:
            source["full_text"] = current + "\n\n" + excerpt
        else:
            source["full_text"] = header + "\n\n" + excerpt
        break
    return candidate


async def prepare_notebook_chat_context(
    context: Dict[str, Any],
    query: str,
    *,
    max_tokens: int,
) -> PreparedNotebookContext:
    """Fit selected notebook context into ``max_tokens`` using source retrieval.

    Small contexts are returned unchanged.  Oversized full-source selections are
    replaced with the most relevant pre-embedded source chunks.  If embeddings
    are unavailable or retrieval fails, the same already-selected source text is
    chunked locally and ranked by literal query overlap.  Every candidate is
    admitted only if the serialized context remains within the budget.
    """
    if max_tokens < CHAT_MIN_CONTEXT_BUDGET:
        raise ValueError(
            f"Notebook chat context budget is too small ({max_tokens} tokens)"
        )

    original_tokens = token_count(str(context))
    if original_tokens <= max_tokens:
        return PreparedNotebookContext(
            context=context,
            original_tokens=original_tokens,
            prepared_tokens=original_tokens,
            mode="full",
        )

    compact, originals = _strip_large_source_text(context)
    _trim_note_content_to_fit(compact, max_tokens)

    # If source metadata/insights/notes alone are still too large, refuse to
    # pretend the request fits.  The caller can surface a clear model-budget
    # error instead of allowing a provider to truncate unpredictably.
    base_tokens = token_count(str(compact))
    if base_tokens >= max_tokens:
        return PreparedNotebookContext(
            context=compact,
            original_tokens=original_tokens,
            prepared_tokens=base_tokens,
            mode="metadata_over_budget",
        )

    selected_ids = list(originals)
    retrieved_by_source: dict[str, list[str]] = {source_id: [] for source_id in selected_ids}
    semantic_retrieval_succeeded = False

    if selected_ids:
        try:
            query_embedding = await generate_embedding(query)
            rows = await retrieve_source_chunks_pg(
                query_embedding,
                selected_ids,
                limit=CHAT_MAX_RETRIEVED_CHUNKS,
            )
            for row in rows:
                source_id = str(row.get("source_id") or "")
                content = str(row.get("content") or "").strip()
                if source_id in retrieved_by_source and content:
                    retrieved_by_source[source_id].append(content)
            semantic_retrieval_succeeded = any(retrieved_by_source.values())
        except Exception as exc:
            logger.warning(f"Notebook chat semantic context retrieval failed: {exc}")

    mode = "retrieved" if semantic_retrieval_succeeded else "lexical_fallback"

    for source_id, original_text in originals.items():
        excerpts = retrieved_by_source.get(source_id) or []
        header = _RETRIEVAL_HEADER
        if not excerpts:
            excerpts = _lexical_chunks(original_text, query)
            header = _FALLBACK_HEADER

        for excerpt in excerpts:
            candidate = _candidate_with_excerpt(compact, source_id, excerpt, header=header)
            candidate_tokens = token_count(str(candidate))
            if candidate_tokens > max_tokens:
                continue
            compact = candidate

    prepared_tokens = token_count(str(compact))
    logger.info(
        "Notebook chat context prepared: "
        f"mode={mode}, original_tokens={original_tokens}, "
        f"prepared_tokens={prepared_tokens}, budget={max_tokens}"
    )
    return PreparedNotebookContext(
        context=compact,
        original_tokens=original_tokens,
        prepared_tokens=prepared_tokens,
        mode=mode,
    )
