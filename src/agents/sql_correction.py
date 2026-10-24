import time
from typing import Any
from logging import Logger
from app_models import FallbackResponse, SqlCorrectionResponse, SubGraph_State

from utils import agent_utils


async def sql_correction(
    state: SubGraph_State,
    llm: Any,
    prompt: str,
    logger: Logger
)-> SubGraph_State  :
    logger.info("Executing sql correction agent.")
    # Render the final prompt string using .format()
    try:
        sql_query = state.text2sql_generator_response.sql_query
        # final_prompt = prompt.format(
        #                         sql_query=sql_query
        #                     )
        messages = [
                {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral","ttl": "1h"}}]},
                {"role": "user", "content": f"""
                                    sql_query: {sql_query}"""}
            ]
        # Send the prompt to the model directly (no chain)
        structured_llm = llm.with_structured_output(SqlCorrectionResponse,include_raw=True)
        # ✅ START TIME
        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        # ✅ END TIME
        end_time = time.time()
        latency = end_time - start_time
        
        sql_correction_response = response_raw["parsed"]
        response_message  = response_raw["raw"]   # AIMessage
        logger.info(f"Sql correction response: {sql_correction_response}\n")

        metric_metadata = agent_utils.agent_metrics(response_message,latency)

        logger.info(f"""Agent: "Sql Correction",
                        Model Used: {metric_metadata.model_used},
                        Latency: {metric_metadata.latency},
                        Model Cost Without Cache: {metric_metadata.model_cost_without_cache},
                        Model Cost With Cache: {metric_metadata.model_cost_with_cache},
                        Input Tokens: {metric_metadata.input_tokens},
                        Output Tokens: {metric_metadata.output_tokens},
                        Total Tokens: {metric_metadata.total_tokens},
                        Cached Tokens (read): {metric_metadata.cached_tokens},
                        Cache Creation Tokens (write): {metric_metadata.cache_creation_tokens}""")

        state.agent_metrics["sql_correction"] = metric_metadata

        # ✅ Write corrected SQL back into the active query so execution reruns it
        if state.text2sql_generator_response:
            state.text2sql_generator_response.sql_query = sql_correction_response.corrected_sql_query
            logger.info(f"Corrected SQL written to state: {sql_correction_response.corrected_sql_query}")

        # ✅ Set loop guard — prevents a second correction attempt if execution fails again
        state.sql_query_correction = True

        # ✅ Clear the fallback so _sql_query_correction router sends flow back to sql_execution
        state.fallback_response = None

    except Exception as e:
        logger.exception(f"Error in sql correction agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="sql_correction",
            fallback_reason="exception",
            fallback_message="I'm sorry, but I'm currently unable to process your request due to a technical issue. Please try again later."
        )
        state.fallback_response = fallback_response
    
    return state 