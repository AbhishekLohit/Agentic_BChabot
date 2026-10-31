from typing import Any
from logging import Logger
from app_models import AgentMetrics, FallbackResponse, Graph_State,SuprivisorResponse
from datetime import datetime
import time

from utils import agent_utils

async def suprivisor_agent(
    state: Graph_State,
    llm: Any,
    prompt: str,
    logger: Logger
)-> Graph_State:
    logger.info("Executing suprivisor agent.")
    # Render the final prompt string using .format()
    try:
        input_query = state.context_builder_response.user_query
        # final_prompt = prompt.format(
        #                         input_query=input_query
        #                     )

        messages = [
                {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral","ttl": "1h"}}]},
                {"role": "user", "content": f"""
                                    input_query: {input_query}"""}
        ]
        # Send the prompt to the model directly (no chain)
        structured_llm = llm.with_structured_output(SuprivisorResponse,include_raw=True)
        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        # response_raw = await structured_llm.ainvoke(final_prompt)
        end_time = time.time()
        latency = end_time - start_time
        
        suprivisor_response = response_raw["parsed"]
        response_message  = response_raw["raw"]   # AIMessage

        state.suprivisor_response = suprivisor_response
        logger.info(f"Suprivisor response: {suprivisor_response}\n")


        metric_metadata = agent_utils.agent_metrics(response_message,latency)

        logger.info(f"""Agent: "Suprivisor",
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
        # state.agent_metrics = AgentMetrics(
        #     context_builder_metrics=None,
        #     suprivisor_metrics=metric_metadata,
        #     classification_metrics=None,
        #     text2sql_generator_metrics=None,
        #     response_generator_metrics=None,
        #     sql_corection_metrics=None,
        # )

        state.agent_metrics["suprivisor"] = metric_metadata

        # Update the state with classified queries
        banksql_queries = []
        out_of_scope = []

        for sub in state.suprivisor_response.classified_sub_queries:
            if sub.class_label == "BankSQL":
                banksql_queries.append(sub.sub_query)
            else:
                out_of_scope.append(sub.sub_query)

        state.banksql_queries = banksql_queries
        state.out_of_scope_queries = out_of_scope
        

    except Exception as e:
        logger.exception(f"Error in supervisor agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="supervisor",
            fallback_reason="exception",
            fallback_message="I'm sorry, but I'm currently unable to process your request due to a technical issue. Please try again later."
        )
        state.fallback_response = fallback_response
    
    return state 