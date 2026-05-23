from typing import Any
from logging import Logger
from app_models import AgentMetrics, FallbackResponse, Graph_State,ResponseGeneratorResponse
from datetime import datetime
import time

from utils import agent_utils

async def response_generator_agent(
    state: Graph_State,
    llm: Any,
    prompt: str,
    logger: Logger
)-> Graph_State:
    logger.info("Executing response generation agent.")
    # Render the final prompt string using .format()
    try:
        final_message = []
        if state.fallback_response:
            final_message.append(state.fallback_response.fallback_message)
        
        if state.database_query_results:
            sql_query_result = state.database_query_results
            user_query = state.context_builder_response.user_query
            # final_prompt = prompt.format(
            #                         sql_query_result=sql_query_result,
            #                         user_question= user_query
            # )
            messages = [
                {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral","ttl": "1h"}}]},
                {"role": "user", "content": f"""
                                    sql_query_result: {sql_query_result}
                                    user_question: {user_query}"""}
            ]

            # Send the prompt to the model directly (no chain)
            structured_llm = llm.with_structured_output(ResponseGeneratorResponse,include_raw=True)

            # ✅ START TIME
            start_time = time.time()
            response_raw = await structured_llm.ainvoke(messages)
            # response_raw = await structured_llm.ainvoke(final_prompt)
             # ✅ END TIME
            end_time = time.time()
            latency = end_time - start_time

            response_generator_response = response_raw["parsed"]
            response_message  = response_raw["raw"]   # AIMessage
            logger.info(f"Response Generator response: {response_generator_response}\n")


            metric_metadata = agent_utils.agent_metrics(response_message,latency)

            logger.info(f"""Agent: "Response Generator",
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
            #     suprivisor_metrics=None,
            #     classification_metrics=None,
            #     text2sql_generator_metrics=None,
            #     response_generator_metrics=metric_metadata,
            #     sql_corection_metrics=None
            # )
            # logger.info(f"Updating state.agent_metrics in response generator agent\nCurrent state.agent_metrics: {state.agent_metrics}")
            state.agent_metrics["response_generator"] = metric_metadata

            final_message.append(response_generator_response.generated_response)
        # Update the state with the response generator response
        state.response_generation_response = ResponseGeneratorResponse(generated_response="\n".join(final_message))
        logger.info(f"Response generation response: {final_message}\n")
    except Exception as e:
        logger.exception(f"Error in response generator agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="response_generator",
            fallback_reason="exception",
            fallback_message="exception in response generator agent"
        )
        state.fallback_response = fallback_response
    return state 