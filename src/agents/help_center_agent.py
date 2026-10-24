from typing import Any
from logging import Logger
import time

from app_models import Graph_State, ResponseGeneratorResponse, FallbackResponse
from utils import agent_utils

# Shown when retrieval returns nothing relevant — keeps the bot grounded instead of
# guessing at a banking procedure.
HELP_CENTRE_URL = "https://www.barclays.co.uk/help/"
NO_RESULT_MESSAGE = (
    "I'm sorry, I couldn't find guidance on that in the Barclays help centre. "
    f"You can search for it here: {HELP_CENTRE_URL}"
)


async def help_center_agent(
    state: Graph_State,
    llm: Any,
    prompt: Any,
    help_store: Any,
    logger: Logger,
) -> Graph_State:
    """
    Strict-RAG help-centre answerer. Retrieves relevant Barclays help passages,
    then asks the LLM to answer ONLY from them and cite the source URL(s). When
    nothing relevant is retrieved (or the store is unavailable), returns a grounded
    "couldn't find it" reply rather than hallucinating.
    """
    logger.info("Executing help center agent.")
    try:
        if not state.help_center_queries:
            return state

        user_query = " ".join(state.help_center_queries)

        # --- Retrieve --------------------------------------------------------
        results = []
        if help_store is not None:
            try:
                results = help_store.search(user_query)
            except Exception as e:
                logger.warning(f"Help store search failed: {e}")
                results = []
        else:
            logger.warning("Help store not configured; returning grounded fallback.")

        # --- No relevant context → grounded fallback (no LLM hallucination) --
        if not results:
            state.help_center_response = ResponseGeneratorResponse(
                generated_response=NO_RESULT_MESSAGE
            )
            logger.info("Help center: no relevant passages retrieved.")
            return state

        # --- Build context block --------------------------------------------
        context_block = "\n\n".join(
            f"[{r.get('source_url')}] {r.get('text')}" for r in results
        )

        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": prompt.template,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            },
            {
                "role": "user",
                "content": f"""
                                    user_question: {user_query}
                                    retrieved_context:
                                    {context_block}""",
            },
        ]

        # Stream the answer as PLAIN TEXT (not structured output) so /process-stream
        # can surface tokens live. Accumulate chunks into the full text + a gathered
        # message for metrics.
        start_time = time.time()
        gathered = None
        async for chunk in llm.astream(messages):
            gathered = chunk if gathered is None else gathered + chunk
        latency = time.time() - start_time

        answer_text = agent_utils.message_to_text(gathered)
        if state.agent_metrics is None:
            state.agent_metrics = {}
        try:
            state.agent_metrics["help_center"] = agent_utils.agent_metrics(gathered, latency)
        except Exception as metric_err:
            logger.warning(f"Help center metrics unavailable: {metric_err}")

        state.help_center_response = ResponseGeneratorResponse(generated_response=answer_text)
        logger.info(f"Help center response: {answer_text}\n")

    except Exception as e:
        logger.exception(f"Error in help center agent: {str(e)}")
        state.fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="help_center",
            fallback_reason="exception",
            fallback_message=(
                "I'm sorry, but I'm currently unable to look that up due to a "
                "technical issue. Please try again later."
            ),
        )
    return state
