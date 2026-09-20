# ADR-008: Model-budgeted notebook chat context

**Status:** Accepted

**Date:** 2026-09-06

## Context

Notebook chat previously treated a source selected as `full content` as an instruction to place that source's entire `full_text` into the system prompt. The context endpoint reported the resulting token count, but inference did not compare that count with the active model's context window.

This is unsafe for local models. A concrete acceptance-test failure used `qwen3:latest` through Ollama: the selected source was approximately 53.5K estimated tokens while the model architecture reports a 40,960-token context window. Normal chat worked, source ingestion worked, and source-backed chat returned an empty assistant message.

The source-chat graph already has source token-budget handling, but notebook chat is a separate path and had no equivalent model-aware boundary.

## Decision

1. Notebook chat will preserve the user's context selection and full token/character count at the context-building/UI stage.
2. Immediately before inference, when Open Notebook can determine the active model's context window reliably, notebook chat will calculate a safe context budget after reserving space for output, the fixed system prompt and accumulated chat history.
3. Ollama is the first provider with runtime context-window discovery. Open Notebook reads architecture metadata from `POST /api/show`; a credential `num_ctx` override is honoured but cannot increase the effective budget beyond the reported architecture limit.
4. If selected notebook context exceeds that budget, full-source bodies are replaced by question-relevant excerpts from the source embeddings already produced during source processing. Retrieval is explicitly scoped to the sources the user selected.
5. If semantic retrieval is unavailable, notebook chat falls back deterministically to local chunking and literal query-overlap ranking of the already-selected source text. It does not fail merely because the embedding provider is temporarily unavailable.
6. Every candidate excerpt is admitted only while the serialized notebook context remains inside the calculated budget.
7. If notes/metadata alone cannot fit, the request fails with an explicit input/context-budget error rather than relying on provider-side truncation.
8. If a model returns no final answer after thinking-content cleanup, the graph raises an explicit external-service error. Empty assistant bubbles are not a valid successful response.

## Consequences

- Local models can answer questions about documents larger than their native context windows without requiring the operator to increase `num_ctx` or replace the model.
- Large-document notebook chat becomes retrieval-augmented rather than prefix-truncated, so relevant material can be selected from anywhere in a source.
- Existing behaviour is unchanged for small contexts.
- Providers whose context-window capability cannot yet be determined retain their existing behaviour; capability discovery can be extended provider by provider without coupling notebook context assembly to provider clients.
- The context count displayed by the UI remains the size of the user's selected material, not the smaller per-question inference payload. This is intentional: selection size and inference payload size are different concepts.
- Source embeddings become part of the preferred large-document chat path, but the lexical fallback preserves graceful operation when embeddings are missing or unavailable.

## Rejected alternatives

### Increase Ollama `num_ctx`

This only moves the failure threshold, consumes additional memory, and cannot exceed a model's architecture limit. It also does not solve the general notebook-chat design defect.

### Blindly truncate the source prefix

This fits the request but can remove the exact material the user is asking about when that material occurs later in the document.

### Reject every oversized source

This is safe but unnecessarily prevents useful local-first document question answering when the source has already been chunked and embedded.
