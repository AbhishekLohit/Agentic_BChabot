import time
from typing import Any
from logging import Logger
from clients.openai_client import get_openai_llm
from app_models import AgentMetrics, FallbackResponse, Graph_State,SqlCorrectionResponse, SubGraph_State
from langchain_openai import ChatOpenAI

from utils import agent_utils


async def sql_correction(
    state: SubGraph_State,
    llm: ChatOpenAI,
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
                {"role": "system", "content": prompt.template},
                {"role": "user", "content": f"""
                                    sql_query: {sql_query}"""}
            ]
        # Send the prompt to the model directly (no chain)
        structured_llm = llm.with_structured_output(SqlCorrectionResponse,include_raw=True)
        # ✅ START TIME
        start_time = time.time()
        # response_raw = await structured_llm.ainvoke(final_prompt)
        response_raw = await structured_llm.ainvoke(
                messages,
                prompt_cache_key="sql-correction-v2"
        )
        # ✅ END TIME
        end_time = time.time()
        latency = end_time - start_time
        
        sql_correction_response = response_raw["parsed"]
        response_message  = response_raw["raw"]   # AIMessage
        state.sql_correction_response = sql_correction_response
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
                        Cached Tokens: {metric_metadata.cached_tokens}""")

        # ✅ Update state
        # state.agent_metrics = AgentMetrics(
        #     context_builder_metrics=None,
        #     suprivisor_metrics=None,
        #     classification_metrics=None,
        #     text2sql_generator_metrics=None,
        #     response_generator_metrics=None,
        #     sql_corection_metrics=metric_metadata
        # )
        state.agent_metrics["sql_correction"] = metric_metadata

    except Exception as e:
        logger.exception(f"Error in supervisor agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="sql_correction",
            fallback_reason="exception",
            fallback_message="exception in sql correction agent"
        )
        state.fallback_response = fallback_response
    
    return state 