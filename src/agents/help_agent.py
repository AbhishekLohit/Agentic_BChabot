from logging import Logger
from typing import Any
import time

from app_models import (
    FallbackResponse,
    Graph_State,
    ResponseGeneratorResponse,
)
from utils import agent_utils
from utils.help_rag import build_help_messages, enforce_citations, retrieve_help_context


NO_DOCUMENTS_MESSAGE = (
    "I couldn't find information about that in the available help documents."
)


async def help_agent(
    state: Graph_State,
    llm: Any,
    prompt: Any,
    vector_store: Any,
    logger: Logger,
) -> Graph_State:
    logger.info("Executing document help agent.")
    try:
        query = state.context_builder_response.user_query
        # Shared with eval/rag/evaluate.py: retrieval mode, top-k and numbering [1]..[n]
        # all come from settings (HELP_RETRIEVAL_MODE, HELP_TOP_K).
        contexts = retrieve_help_context(vector_store, query)

        if not contexts:
            state.response_generation_response = ResponseGeneratorResponse(
                generated_response=NO_DOCUMENTS_MESSAGE
            )
            return state

        messages = build_help_messages(prompt.template, query, contexts)
        structured_llm = llm.with_structured_output(
            ResponseGeneratorResponse,
            include_raw=True,
        )
        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        latency = time.time() - start_time
        # Strict citations: drop citations to excerpts that don't exist, drop sentences
        # with no valid citation, and refuse if nothing in the answer is supported.
        final_answer, citation_report = enforce_citations(
            response_raw["parsed"].generated_response, len(contexts))
        if citation_report["invalid_citations"] or citation_report["dropped_sentences"] or citation_report["refused"]:
            logger.info(f"Citation check: {citation_report}")
        state.response_generation_response = ResponseGeneratorResponse(generated_response=final_answer)

        if state.agent_metrics is None:
            state.agent_metrics = {}
        state.agent_metrics["help_agent"] = agent_utils.agent_metrics(
            response_raw["raw"], latency
        )
    except Exception:
        logger.exception("Error in document help agent")
        state.fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="help_agent",
            fallback_reason="exception",
            fallback_message=(
                "I'm sorry, but I'm currently unable to answer from the help "
                "documents right now. Please try again later."
            ),
        )
    return state
