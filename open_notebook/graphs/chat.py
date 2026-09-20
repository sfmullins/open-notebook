import asyncio
import sqlite3
from typing import Annotated, Optional

from ai_prompter import Prompter
from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from open_notebook.ai.context_window import get_chat_model_context_window
from open_notebook.ai.provision import provision_langchain_model
from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import (
    ExternalServiceError,
    InvalidInputError,
    OpenNotebookError,
)
from open_notebook.utils import clean_thinking_content, token_count
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.notebook_chat_context import prepare_notebook_chat_context
from open_notebook.utils.text_utils import extract_text_content

CHAT_MAX_OUTPUT_TOKENS = 8192
CHAT_CONTEXT_SAFETY_RATIO = 0.85
CHAT_MIN_CONTEXT_TOKENS = 1024


class ThreadState(TypedDict):
    messages: Annotated[list, add_messages]
    notebook: Optional[Notebook]
    context: Optional[dict]
    context_config: Optional[dict]
    model_override: Optional[str]


def _latest_user_query(messages: list) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) in {"human", "user"}:
            return extract_text_content(getattr(message, "content", ""))
    return ""


async def _prepare_payload_and_model(
    state: ThreadState,
    model_id: Optional[str],
):
    working_state = dict(state)
    messages = list(state.get("messages", []))
    context = state.get("context")

    context_window = await get_chat_model_context_window(model_id)
    if context_window and isinstance(context, dict) and context:
        # Reserve output, fixed system-prompt overhead, and the accumulated
        # conversation before allocating any space to selected notebook data.
        output_reserve = min(
            CHAT_MAX_OUTPUT_TOKENS,
            max(1024, context_window // 5),
        )
        fixed_prompt_reserve = min(4096, max(1024, context_window // 10))
        history_tokens = token_count(str(messages))
        raw_context_budget = (
            context_window - output_reserve - fixed_prompt_reserve - history_tokens
        )
        context_budget = int(raw_context_budget * CHAT_CONTEXT_SAFETY_RATIO)

        if context_budget < CHAT_MIN_CONTEXT_TOKENS:
            raise InvalidInputError(
                "The current chat history leaves too little room for notebook context "
                f"in this model's {context_window}-token context window. Start a new "
                "chat session or choose a model with a larger context window."
            )

        prepared = await prepare_notebook_chat_context(
            context,
            _latest_user_query(messages),
            max_tokens=context_budget,
        )
        if prepared.prepared_tokens > context_budget:
            raise InvalidInputError(
                "The selected notebook metadata and notes exceed the active model's "
                f"context budget ({context_budget} tokens available from a "
                f"{context_window}-token window). Reduce the selected context or "
                "choose a model with a larger context window."
            )
        working_state["context"] = prepared.context

    system_prompt = Prompter(prompt_template="chat/system").render(data=working_state)  # type: ignore[arg-type]
    payload = [SystemMessage(content=system_prompt)] + messages
    model = await provision_langchain_model(
        str(payload),
        model_id,
        "chat",
        max_tokens=CHAT_MAX_OUTPUT_TOKENS,
    )
    return payload, model


def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        model_id = config.get("configurable", {}).get("model_id") or state.get(
            "model_override"
        )

        # Handle async context preparation/model provisioning from sync graph node.
        def run_in_new_loop():
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(
                    _prepare_payload_and_model(state, model_id)
                )
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)

        try:
            asyncio.get_running_loop()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_new_loop)
                payload, model = future.result()
        except RuntimeError:
            payload, model = asyncio.run(_prepare_payload_and_model(state, model_id))

        ai_message = model.invoke(payload)

        # Clean thinking content from AI response (e.g., <think>...</think> tags).
        content = extract_text_content(ai_message.content)
        cleaned_content = clean_thinking_content(content)
        if not cleaned_content.strip():
            raise ExternalServiceError(
                "The model returned no final answer. Its response may have exhausted "
                "the available context/output budget. Reduce the selected context, "
                "start a new chat session, or choose a model with a larger context window."
            )
        cleaned_message = ai_message.model_copy(update={"content": cleaned_content})

        return {"messages": cleaned_message}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


conn = sqlite3.connect(
    LANGGRAPH_CHECKPOINT_FILE,
    check_same_thread=False,
)
memory = SqliteSaver(conn)

agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile(checkpointer=memory)
