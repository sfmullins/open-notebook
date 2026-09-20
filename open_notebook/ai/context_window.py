"""Resolve provider/model context-window limits for chat budgeting.

Notebook chat must budget selected source material against the model that will
actually receive the prompt.  The generic model record does not currently
store a context-window capability, so this module resolves the value when the
provider can report it reliably.

Ollama is the first supported capability source because it is both local-first
and exposes the architecture context length through ``POST /api/show``.  A
credential ``num_ctx`` override is honoured and never allowed to exceed the
reported architecture limit.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from loguru import logger

from open_notebook.ai.models import Model, model_manager
from open_notebook.utils.url_validation import prepare_pinned_http_target


async def _resolve_chat_model(model_id: Optional[str]) -> Optional[Model]:
    resolved_id = model_id
    if not resolved_id:
        defaults = await model_manager.get_defaults()
        resolved_id = defaults.default_chat_model
    if not resolved_id:
        return None
    try:
        return await Model.get(resolved_id)
    except Exception as exc:
        logger.warning(f"Could not resolve chat model {resolved_id}: {exc}")
        return None


def _ollama_architecture_context_length(payload: dict[str, Any]) -> Optional[int]:
    model_info = payload.get("model_info")
    if not isinstance(model_info, dict):
        return None

    lengths: list[int] = []
    for key, value in model_info.items():
        if not str(key).endswith(".context_length"):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            lengths.append(parsed)
    return max(lengths) if lengths else None


async def get_chat_model_context_window(model_id: Optional[str]) -> Optional[int]:
    """Return the effective context window for a chat model when discoverable.

    ``None`` means the provider did not expose a trustworthy capability and the
    caller should preserve the existing provider behaviour rather than guess.
    """
    model = await _resolve_chat_model(model_id)
    if model is None or model.provider.lower() != "ollama":
        return None

    configured_num_ctx: Optional[int] = None
    base_url = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")

    if model.credential:
        try:
            credential = await model.get_credential_obj()
            if credential is not None:
                config = credential.to_esperanto_config()
                if config.get("base_url"):
                    base_url = str(config["base_url"])
                raw_num_ctx = config.get("num_ctx")
                if raw_num_ctx is not None:
                    parsed_num_ctx = int(raw_num_ctx)
                    if parsed_num_ctx > 0:
                        configured_num_ctx = parsed_num_ctx
        except Exception as exc:
            logger.warning(
                f"Could not read Ollama credential context settings for {model.name}: {exc}"
            )

    try:
        target = await prepare_pinned_http_target(
            f"{base_url.rstrip('/')}/api/show", "ollama"
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                target.url,
                json={"model": model.name},
                headers=dict(target.headers),
                timeout=10.0,
                extensions=target.extensions,
            )
            response.raise_for_status()
            architecture_limit = _ollama_architecture_context_length(response.json())
    except Exception as exc:
        logger.warning(
            f"Could not discover Ollama context window for {model.name}: {exc}"
        )
        return configured_num_ctx

    if architecture_limit is None:
        return configured_num_ctx
    if configured_num_ctx is None:
        return architecture_limit
    return min(configured_num_ctx, architecture_limit)
