from typing import Any
from logging import Logger
from agents.disputes.workflow import plain_reply as dispute_plain_reply
from app_models import FallbackResponse, Graph_State,ResponseGeneratorResponse
import time
from langchain_core.prompts import BasePromptTemplate

from utils import agent_utils
from utils.chart_intent import is_explicit_pie_request

async def _dispute_reply(state: Graph_State, llm: Any, prompt: Any, logger: Logger) -> str:
    """Put the Disputes agent's result into words. The agent has already decided what
    happens; this only phrases it, and falls back to a plain fixed wording if the model
    is unavailable."""
    result = state.dispute_result
    if prompt is None:
        return dispute_plain_reply(result)

    user_block = f"status: {result.status}\nsummary:\n{result.summary}\n"
    if result.status == "suspected_fraud":
        user_block += f"hand_off_message: {result.data.get('handoff_message', '')}\n"
    # Whether this message continues a dispute that was already open, so the reply does not greet
    # the customer again. The chat history is deliberately NOT passed: it can hold earlier chats
    # (even other sessions) and the reply must take every fact from the summary alone.
    user_block += f"dispute_in_progress: {'yes' if state.dispute_open else 'no'}\n"
    if state.pipeline_query:
        user_block += (
            f'another_request_answered_separately: "{state.pipeline_query}"\n'
        )
    user_block += f"customer_message: {state.input_request.user_query}"
    messages = [
        {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]},
        {"role": "user", "content": user_block},
    ]
    try:
        start_time = time.time()
        gathered = None
        async for chunk in llm.astream(messages):
            gathered = chunk if gathered is None else gathered + chunk
        latency = time.time() - start_time
        text = agent_utils.message_to_text(gathered).strip()
        try:
            if state.agent_metrics is not None:
                agent_utils.record_metric(state.agent_metrics, "dispute_reply", agent_utils.agent_metrics(gathered, latency))
        except Exception as metric_err:
            logger.warning(f"Dispute reply metrics unavailable: {metric_err}")
    except Exception:
        logger.exception("Dispute reply writer failed; using the plain wording.")
        return dispute_plain_reply(result)

    if not text:
        return dispute_plain_reply(result)
    # The case ID is the one fact the customer must not lose to rephrasing.
    case_id = result.data.get("case_id")
    if result.status == "filed" and case_id and case_id not in text:
        text += f"\nYour case ID is {case_id}."
    return text


async def response_generator_agent(
    state: Graph_State,
    llm: Any,
    prompt: str,
    logger: Logger,
    dispute_prompt: Any = None,
)-> Graph_State:
    logger.info("Executing response generation agent.")
    if state.visualization_only:
        state.response_generation_response = ResponseGeneratorResponse(
            generated_response=""
        )
        logger.info("Skipping text response because the user requested only a visualization.")
        return state

    # Render the final prompt string using .format()
    try:
        final_message = []
        if state.fallback_response:
            final_message.append(state.fallback_response.fallback_message)

        # A dispute turn: the Disputes agent decided what happens, this puts it in words.
        if state.dispute_result:
            final_message.append(await _dispute_reply(state, llm, dispute_prompt, logger))

        # Help-centre answers are produced (with source links) by the help_center
        # agent itself — just pass them through to the final reply. No extra LLM call.
        if state.help_center_response:
            final_message.append(state.help_center_response.generated_response)

        if state.database_query_results is not None:
            sql_query_result = state.database_query_results
            truncation_notice = (
                f"\n[System Notification: Only {len(sql_query_result)} result rows are displayed; "
                "additional results were omitted.]"
                if state.database_results_truncated
                else ""
            )
            # The queries answered on the SQL path are the classified BankSQL queries
            # (one, or several combined into a single SQL); fall back to the
            # context-builder queries if unavailable.
            if state.banksql_queries:
                user_query = " ".join(state.banksql_queries)
            elif state.context_builder_response:
                user_query = " ".join(state.context_builder_response.contextual_queries)
            else:
                user_query = ""
            # final_prompt = prompt.format(
            #                         sql_query_result=sql_query_result,
            #                         user_question= user_query
            # )
            messages = [
                {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral","ttl": "1h"}}]},
                {"role": "user", "content": f"""
                                    sql_query_result: {sql_query_result}
                                    {truncation_notice}
                                    user_question: {user_query}"""}
            ]

            # Stream the answer token-by-token as PLAIN TEXT (not structured output) so
            # the /process-stream endpoint can surface tokens live via LangGraph's
            # "messages" stream mode. We accumulate the chunks into both the full reply
            # text and a single gathered message (for usage/metrics).
            start_time = time.time()
            gathered = None
            async for chunk in llm.astream(messages):
                gathered = chunk if gathered is None else gathered + chunk
            latency = time.time() - start_time

            generated_text = agent_utils.message_to_text(gathered)
            logger.info(f"Response Generator response: {generated_text}\n")

            # Best-effort metrics — streaming chunks expose usage_metadata when available.
            try:
                state.agent_metrics["response_generator"] = agent_utils.agent_metrics(gathered, latency)
            except Exception as metric_err:
                logger.warning(f"Response generator metrics unavailable: {metric_err}")

            final_message.append(generated_text)
        if (
            state.input_request is not None
            and is_explicit_pie_request(state.input_request.user_query)
            and state.visualization_response is not None
            and not state.visualization_response.decision.should_visualize
            and state.visualization_response.decision.reasoning.startswith(
                "Pie chart not supported for these values:"
            )
        ):
            final_message.append(state.visualization_response.decision.reasoning)

        # After a side question asked mid-dispute: say the dispute is still open.
        if state.dispute_reminder:
            final_message.append(state.dispute_reminder)
        # Dispute replies are paragraphs, so they are set apart from the other answers by a
        # blank line. Every other reply keeps the single newline it always had.
        separator = "\n\n" if (state.dispute_result or state.dispute_reminder) else "\n"
        # Update the state with the response generator response
        state.response_generation_response = ResponseGeneratorResponse(generated_response=separator.join(final_message))
        logger.info(f"Response generation response: {final_message}\n")
    except Exception as e:
        logger.exception(f"Error in response generator agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="response_generator",
            fallback_reason="exception",
            fallback_message="I'm sorry, but I'm currently unable to process your request due to a technical issue. Please try again later."
        )
        state.fallback_response = fallback_response
    return state 