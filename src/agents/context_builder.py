from typing import Any
from logging import Logger
from clients.openai_client import get_openai_llm
from app_models import AgentMetrics, FallbackResponse, Graph_State,ContextBuilderResponse,Metrics
from langchain_openai import ChatOpenAI
from datetime import datetime
import json
import time   # ✅ ADDED
from utils import agent_utils 

async def context_builder_agent(
    state: Graph_State,
    llm: ChatOpenAI,
    prompt: str,
    logger: Logger
)-> Graph_State:
    logger.info("Executing context builder agent.")
    
    try:
        input_query = state.input_request.user_query
        logger.info(f"Chat history in context builder agent: {state.chat_history}")
    
        messages = [
            {"role": "system", "content": prompt.template},
            {"role": "user", "content": f"""
                                Latest user question: {input_query}
                                Chat history: {state.chat_history}"""}
        ]

        structured_llm = llm.with_structured_output(ContextBuilderResponse, include_raw=True)

        # ✅ START TIME
        start_time = time.time()

        response_raw = await structured_llm.ainvoke(
            messages,
            prompt_cache_key="context-builder-v2"
        )

        # ✅ END TIME
        end_time = time.time()
        latency = end_time - start_time

        contextual_query = response_raw["parsed"]
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
                        Cached Tokens: {metric_metadata.cached_tokens}""")

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

        state.context_builder_response = contextual_query
        
    except Exception as e:
        logger.exception(f"Error in context builder agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="context_builder",
            fallback_reason="exception",
            fallback_message="exception in context builder agent"
        )
        state.fallback_response = fallback_response
    
    return state