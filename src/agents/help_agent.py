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


def _help_question(state: Graph_State) -> str:
    """The help question(s) to answer: the supervisor's HelpCenter queries, else the
    RAG-branch help_queries, else the context builder's queries."""
    queries = state.help_center_queries or state.help_queries
    if not queries and state.context_builder_response is not None:
        queries = state.context_builder_response.contextual_queries
    return " ".join(queries or [])


async def help_agent(
    state: Graph_State,
    llm: Any,
    prompt: Any,
    vector_store: Any,
    logger: Logger,
) -> Graph_State:
    """RAG help-document agent: retrieve numbered excerpts from data/help_documents
    (dense / bm25 / hybrid / hybrid_rerank, see HELP_RETRIEVAL_MODE), answer from them
    only, then enforce citations in code (HELP_CITATION_POLICY).

    The answer goes to state.help_center_response, which the response generator passes
    through to the final reply."""
    logger.info("Executing document help agent.")
    try:
        query = _help_question(state)
        if not query:
            return state

        # Shared with eval/rag/evaluate.py: retrieval mode, top-k and numbering [1]..[n]
        # all come from settings (HELP_RETRIEVAL_MODE, HELP_TOP_K).
        contexts = retrieve_help_context(vector_store, query)
        state.help_source = "help_documents"
        state.help_contexts = contexts

        if not contexts:
            state.help_center_response = ResponseGeneratorResponse(
                generated_response=NO_DOCUMENTS_MESSAGE
            )
            logger.info("Help documents: no relevant passages retrieved.")
            return state

        messages = build_help_messages(prompt.template, query, contexts)
        structured_llm = llm.with_structured_output(
            ResponseGeneratorResponse,
            include_raw=True,
        )
        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        latency = time.time() - start_time
        if response_raw.get("parsed") is None:
            raise ValueError(f"help agent: unparsable LLM output: {str(response_raw.get('raw'))[:200]}")

        # Strict citations: drop citations to excerpts that don't exist, drop sentences
        # with no valid citation, and refuse if nothing in the answer is supported.
        final_answer, citation_report = enforce_citations(
            response_raw["parsed"].generated_response, len(contexts))
        if citation_report["invalid_citations"] or citation_report["dropped_sentences"] or citation_report["refused"]:
            logger.info(f"Citation check: {citation_report}")
        state.help_citation_report = citation_report
        state.help_center_response = ResponseGeneratorResponse(generated_response=final_answer)
        logger.info(f"Help documents response: {final_answer}\n")

        if state.agent_metrics is None:
            state.agent_metrics = {}
        try:
            # "help_center" so the UI's agent trace and /process-stream pick it up.
            state.agent_metrics["help_center"] = agent_utils.agent_metrics(response_raw["raw"], latency)
        except Exception as metric_err:
            logger.warning(f"Help agent metrics unavailable: {metric_err}")
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
