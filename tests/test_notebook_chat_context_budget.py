from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from open_notebook.ai.context_window import _ollama_architecture_context_length
from open_notebook.exceptions import ExternalServiceError
from open_notebook.graphs.chat import call_model_with_messages
from open_notebook.utils.notebook_chat_context import prepare_notebook_chat_context


def test_ollama_context_length_parser_uses_architecture_metadata():
    payload = {
        "model_info": {
            "general.architecture": "qwen3",
            "qwen3.context_length": 40960,
            "qwen3.embedding_length": 4096,
        }
    }
    assert _ollama_architecture_context_length(payload) == 40960


@pytest.mark.asyncio
async def test_small_notebook_context_is_unchanged():
    context = {
        "sources": [{"id": "source:1", "title": "Small", "full_text": "short body"}],
        "notes": [],
    }

    result = await prepare_notebook_chat_context(
        context,
        "what is this?",
        max_tokens=2048,
    )

    assert result.mode == "full"
    assert result.context is context
    assert result.prepared_tokens == result.original_tokens


@pytest.mark.asyncio
async def test_oversized_source_uses_scoped_semantic_chunks():
    context = {
        "sources": [
            {
                "id": "source:car",
                "title": "Vehicle guide",
                "full_text": "background " * 12000,
                "insights": [],
            }
        ],
        "notes": [],
    }
    retrieved = [
        {
            "id": "source_embedding:1",
            "source_id": "source:car",
            "order": 42,
            "content": "The 350GT uses the VQ35 engine and is described here.",
            "similarity": 0.91,
        }
    ]

    with (
        patch(
            "open_notebook.utils.notebook_chat_context.generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2, 0.3]),
        ),
        patch(
            "open_notebook.utils.notebook_chat_context.retrieve_source_chunks_pg",
            new=AsyncMock(return_value=retrieved),
        ) as retrieve,
    ):
        result = await prepare_notebook_chat_context(
            context,
            "tell me about the 350gt",
            max_tokens=1600,
        )

    assert result.mode == "retrieved"
    assert result.prepared_tokens <= 1600
    full_text = result.context["sources"][0]["full_text"]
    assert "350GT" in full_text
    assert "Question-relevant excerpts" in full_text
    retrieve.assert_awaited_once()
    assert retrieve.await_args.args[1] == ["source:car"]


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_query_relevant_local_chunk():
    unique = "The Nissan Skyline 350GT section describes the VQ35 drivetrain in detail."
    context = {
        "sources": [
            {
                "id": "source:car",
                "title": "Vehicle guide",
                "full_text": ("unrelated material. " * 9000) + unique,
                "insights": [],
            }
        ],
        "notes": [],
    }

    with patch(
        "open_notebook.utils.notebook_chat_context.generate_embedding",
        new=AsyncMock(side_effect=RuntimeError("embedding unavailable")),
    ):
        result = await prepare_notebook_chat_context(
            context,
            "tell me about the 350gt",
            max_tokens=1600,
        )

    assert result.mode == "lexical_fallback"
    assert result.prepared_tokens <= 1600
    full_text = result.context["sources"][0]["full_text"]
    assert "350GT" in full_text
    assert "Locally selected excerpts" in full_text


def test_empty_final_answer_is_not_returned_as_blank_chat_message():
    model = MagicMock()
    model.invoke.return_value = AIMessage(content="<think>reasoning only</think>")

    async def prepared(_state, _model_id):
        return [HumanMessage(content="question")], model

    state = {
        "messages": [HumanMessage(content="question")],
        "notebook": None,
        "context": None,
        "context_config": None,
        "model_override": None,
    }

    with patch(
        "open_notebook.graphs.chat._prepare_payload_and_model",
        new=prepared,
    ):
        with pytest.raises(ExternalServiceError, match="no final answer"):
            call_model_with_messages(state, {"configurable": {}})
