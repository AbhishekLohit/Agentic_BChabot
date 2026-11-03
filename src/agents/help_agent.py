from logging import Logger
from typing import Any
import time

from app_models import (
    FallbackResponse,
    Graph_State,
    ResponseGeneratorResponse,
)
from utils import agent_utils


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
        results = vector_store.similarity_search_with_relevance_scores(query, k=4)
        context_parts = []
        for document, score in results:
            if score <= 0:
                continue
            source = document.metadata.get("source", "unknown")
            page = document.metadata.get("page")
            citation = f"{source}, page {page}" if page else source
            context_parts.append(
                f"[Source: {citation}]\n{document.page_content}"
            )

        if not context_parts:
            state.response_generation_response = ResponseGeneratorResponse(
                generated_response=NO_DOCUMENTS_MESSAGE
            )
            return state

        messages = [
            {
                "role": "system",
                "content": prompt.template,
            },
            {
                "role": "user",
                "content": (
                    f"Question: {query}\n\n"
                    f"Retrieved document excerpts:\n"
                    f"{chr(10).join(context_parts)}"
                ),
            },
        ]
        structured_llm = llm.with_structured_output(
            ResponseGeneratorResponse,
            include_raw=True,
        )
        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        latency = time.time() - start_time
        state.response_generation_response = response_raw["parsed"]

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
