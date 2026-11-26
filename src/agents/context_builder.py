from typing import Any
from logging import Logger
from app_models import FallbackResponse, Graph_State,ContextBuilderResponse
from datetime import datetime
import re
import time
from utils import agent_utils

# A clarification reply starts with this sentence, so the next turn can tell that the
# previous reply was a clarification (and never ask a second one in a row).
CLARIFICATION_LEAD = "I want to make sure I answer the right question."
# Long replies (e.g. a 20-row transaction list) are cut so the history fits Qwen's 8K window.
MAX_HISTORY_MESSAGE_CHARS = 1500
# Total budget for the history (~2K tokens). With up to 10 turns before summarising, the
# oldest messages are left out first; the newest always fit.
MAX_HISTORY_TOTAL_CHARS = 8000


def _prior_history(state: Graph_State) -> list:
    """The chat history WITHOUT the current message (which is always appended last)."""
    history = list(state.chat_history or [])
    current = state.input_request.user_query if state.input_request else None
    if history and history[-1].role == "user" and history[-1].content == current:
        history = history[:-1]
    return history


def _format_history(history: list) -> str:
    if not history:
        return "(none - this is the first message)"
    lines, used = [], 0
    for msg in reversed(history):              # newest first, so the oldest are dropped
        content = (msg.content or "").strip()
        if len(content) > MAX_HISTORY_MESSAGE_CHARS:
            content = content[:MAX_HISTORY_MESSAGE_CHARS] + " ..."
        line = f"{'User' if msg.role == 'user' else 'Assistant'}: {content}"
        if lines and used + len(line) > MAX_HISTORY_TOTAL_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(reversed(lines))


def _clarification_message(question: str) -> str:
    return f"{CLARIFICATION_LEAD} {question}"


# A rewrite that still says "them"/"these"/"those" has not resolved the reference. With
# history to resolve it from, the model is asked once more (Qwen3-8B sometimes leaves it).
_UNRESOLVED = re.compile(r"\b(them|these|those)\b", re.IGNORECASE)


def _unresolved_words(queries: list[str]) -> list[str]:
    return sorted({m.group(1).lower() for q in queries for m in _UNRESOLVED.finditer(q or "")})


async def context_builder_agent(
    state: Graph_State,
    llm: Any,
    prompt: str,
    logger: Logger
)-> Graph_State:
    logger.info("Executing context builder agent.")
    
    try:
        # pipeline_query is set when part of the message was a reply to an open dispute and
        # only the rest should go through the normal pipeline.
        input_query = state.pipeline_query or state.input_request.user_query
        prior = _prior_history(state)
        logger.info(f"Chat history in context builder agent: {prior}")
        after_clarification = bool(
            prior and prior[-1].role == "assistant"
            and (prior[-1].content or "").startswith(CLARIFICATION_LEAD)
        )

        today_str = datetime.now().strftime("%Y-%m-%d")
        messages = [
            {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]},
            {"role": "user", "content": (
                f"Today's date: {today_str}\n\n"
                f"Chat history (earlier turns, oldest first):\n{_format_history(prior)}\n\n"
                f"Latest user question: {input_query}"
            )},
        ]

        structured_llm = llm.with_structured_output(ContextBuilderResponse, include_raw=True)

        # ✅ START TIME
        start_time = time.time()

        response_raw = await structured_llm.ainvoke(messages)
        contextual_query = response_raw["parsed"]

        first_queries = getattr(contextual_query, "contextual_queries", None) or []
        leftover = _unresolved_words(first_queries) if prior else []
        if leftover:
            words = ", ".join(f'"{w}"' for w in leftover)
            logger.info(f"Context builder left {words} unresolved; asking once more.")
            retry_raw = await structured_llm.ainvoke(messages + [{"role": "user", "content": (
                f"Your query still contains {words}. Replace it with what it refers to in the "
                "chat history (for a list in the last Assistant reply, write out the listed "
                "names). Keep everything else the same."
            )}])
            retry = retry_raw.get("parsed") if isinstance(retry_raw, dict) else None
            retry_queries = getattr(retry, "contextual_queries", None) or []
            if retry_queries and not _unresolved_words(retry_queries):
                contextual_query, response_raw = retry, retry_raw

        # ✅ END TIME
        end_time = time.time()
        latency = end_time - start_time
        response_message  = response_raw["raw"]
        logger.info(f"Context Builder response: {contextual_query}\n")

        metric_metadata = agent_utils.agent_metrics(response_message,latency)

        logger.info(f"""Agent: "Context Builder",
                        Model Used: {metric_metadata.model_used},
                        Latency: {metric_metadata.latency},
                        Model Cost Without Cache: {metric_metadata.model_cost_without_cache},
                        Model Cost With Cache: {metric_metadata.model_cost_with_cache},
                        Input Tokens: {metric_metadata.input_tokens},
                        Output Tokens: {metric_metadata.output_tokens},
                        Total Tokens: {metric_metadata.total_tokens},
                        Cached Tokens (read): {metric_metadata.cached_tokens},
                        Cache Creation Tokens (write): {metric_metadata.cache_creation_tokens}""")

        # ✅ Update state
        # ✅ FIX: Store in dict
        if state.agent_metrics is None:
            state.agent_metrics = {}

        state.agent_metrics["context_builder"] = metric_metadata

        # state.agent_metrics = AgentMetrics(
        #     context_builder_metrics=metric_metadata,
        #     suprivisor_metrics=None,
        #     classification_metrics=None,
        #     text2sql_generator_metrics=None,
        #     response_generator_metrics=None,
        #     sql_corection_metrics=None
        # )

        queries = [q.strip() for q in (getattr(contextual_query, "contextual_queries", None) or [])
                   if q and q.strip()]
        question = (getattr(contextual_query, "clarification_question", None) or "").strip()
        if queries:
            # A usable rewrite always wins over a clarification question.
            state.context_builder_response = ContextBuilderResponse(contextual_queries=queries)
        elif question and not after_clarification:
            logger.info(f"Context builder asks for clarification: {question}")
            state.context_builder_response = ContextBuilderResponse(
                contextual_queries=[], clarification_question=question
            )
            state.fallback_response = FallbackResponse(
                fallback_flag=True,
                fallback_node="context_builder",
                fallback_reason="clarification",
                fallback_message=_clarification_message(question),
            )
        else:
            # Nothing usable (or a second clarification in a row): go on with the
            # customer's own words rather than failing or asking again.
            logger.info("Context builder returned no query; using the message as it is.")
            state.context_builder_response = ContextBuilderResponse(contextual_queries=[input_query])

    except Exception as e:
        logger.exception(f"Error in context builder agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="context_builder",
            fallback_reason="exception",
            fallback_message="I'm sorry, but I'm currently unable to process your request due to a technical issue. Please try again later."
        )
        state.fallback_response = fallback_response
    
    return state