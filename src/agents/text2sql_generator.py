from logging import Logger
import hashlib
import re
import time
from app_models import FallbackResponse, SubGraph_State, Text2SqlGeneratorResponse
from typing import Any

from utils import agent_utils
from utils import db_utils


def _make_cache_key(customer_id: str, nl_query: str) -> str:
    norm = re.sub(r'\s+', ' ', nl_query.lower().strip())
    digest = hashlib.sha256(f"{customer_id}:{norm}".encode()).hexdigest()[:16]
    return f"qcache:{customer_id}:{digest}"


async def text2sql_generator_agent(
    state: SubGraph_State,
    llm: Any,
    prompt: str,
    logger: Logger,
    redis_client: Any = None,
    db_conn: Any = None,
)-> SubGraph_State:
    logger.info("Executing text2sql generator agent.")
    try:
        input_query = state.banksql_queries
        query_str = str(input_query)
        customer_id = state.input_request.customer_id
        logger.info(f"Input query for Text2SQL Generator: {input_query}")
        logger.info(f"Customer ID for Text2SQL Generator: {customer_id}")

        # --- CACHE CHECK ---
        cache_key = _make_cache_key(customer_id, query_str)
        cached_sql = None
        if redis_client is not None:
            try:
                cached_sql = redis_client.get(cache_key)
            except Exception:
                pass
        if cached_sql is None and db_conn is not None:
            cached_sql = db_utils.get_cached_sql(db_conn, cache_key)
            if cached_sql and redis_client is not None:
                try:
                    redis_client.set(cache_key, cached_sql, ex=86400)
                except Exception:
                    pass
        if cached_sql:
            logger.info(f"[text2sql] Cache HIT for: {query_str[:80]}")
            state.text2sql_generator_response = Text2SqlGeneratorResponse(
                user_query=query_str, sql_query=cached_sql
            )
            return state
        # --- END CACHE CHECK ---

        messages = [
            {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral","ttl": "1h"}}]},
            {"role": "user", "content": f"""
                                input_query: {input_query}
                                customer_id: {customer_id}"""}
        ]
        structured_llm = llm.with_structured_output(Text2SqlGeneratorResponse,include_raw=True)
        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        end_time = time.time()
        latency = end_time - start_time
        sql_generator = response_raw["parsed"]
        response_message  = response_raw["raw"]   # AIMessage

        state.text2sql_generator_response = sql_generator
        logger.info(f"Text2SQL Generator response: {sql_generator}\n")

        metric_metadata = agent_utils.agent_metrics(response_message,latency)
        state.agent_metrics["text2sql_generator"] = metric_metadata

        # Multiple distinct intents that CANNOT be answered by one combined SQL query →
        # ask the user to split them. (Single-intent inputs are always answerable.)
        if input_query and len(input_query) > 1 and not sql_generator.can_answer_with_single_query:
            logger.info(
                "Text2SQL: multiple intents cannot be combined into a single query — falling back."
            )
            state.fallback_response = FallbackResponse(
                fallback_flag=True,
                fallback_node="text2sql_generator",
                fallback_reason="needs_separate_questions",
                fallback_message=None,
            )
            return state

        logger.info(f"""Agent: "Text2SQL Generator",
                        Model Used: {metric_metadata.model_used},
                        Latency: {metric_metadata.latency},
                        Model Cost Without Cache: {metric_metadata.model_cost_without_cache},
                        Model Cost With Cache: {metric_metadata.model_cost_with_cache},
                        Input Tokens: {metric_metadata.input_tokens},
                        Output Tokens: {metric_metadata.output_tokens},
                        Total Tokens: {metric_metadata.total_tokens},
                        Cached Tokens (read): {metric_metadata.cached_tokens},
                        Cache Creation Tokens (write): {metric_metadata.cache_creation_tokens}""")

        logger.info(f"state.agent_metrics updated in text2sql generator agent\n{state.agent_metrics}")

        # --- CACHE WRITE ---
        generated_sql = sql_generator.sql_query
        if db_conn is not None:
            try:
                db_utils.save_cached_sql(db_conn, cache_key, customer_id, query_str, generated_sql)
            except Exception:
                pass
        if redis_client is not None:
            try:
                redis_client.set(cache_key, generated_sql, ex=86400)
            except Exception:
                pass
        # --- END CACHE WRITE ---

    except Exception as e:
        logger.exception(f"Error in text2sql generator agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="text2sql_generator",
            fallback_reason="exception",
            fallback_message="I'm sorry, but I'm currently unable to process your request due to a technical issue. Please try again later."
        )
        state.fallback_response = fallback_response
    return state
