from email import message
from logging import Logger
import hashlib
import re
import time
from app_models import AgentMetrics, FallbackResponse, Graph_State, SubGraph_State, Text2SqlGeneratorResponse
from typing import Any
import sqlite3

from utils import agent_utils
from utils import db_utils


def _make_cache_key(customer_id: str, nl_query: str) -> str:
    norm = re.sub(r'\s+', ' ', nl_query.lower().strip())
    digest = hashlib.sha256(f"{customer_id}:{norm}".encode()).hexdigest()[:16]
    return f"qcache:{customer_id}:{digest}"


def execute_query(
    sql_query: str,
    db_conn: any,
    logger: Logger
) -> list[str]:
    """
    Execute the given SQL query and return results as a string.
    """
    cursor = db_conn.cursor()
    try:
        cursor.execute(sql_query)
        if sql_query.strip().lower().startswith("select"):
            # Return results as list of dicts
            columns = [col[0] for col in cursor.description]
            results = [str(dict(zip(columns, row))) for row in cursor.fetchall()]
            return results
        else:
            db_conn.commit()
            return ["invalid_query"]
    except sqlite3.Error as e:
        logger.info(f"SQL Error: {e}")
        return ["wrong_query"]
    finally:
        cursor.close()

async def text2sql_generator_agent(
    state: SubGraph_State,
    llm: Any,
    prompt: str,
    logger: Logger,
    db_conn: Any = None,
)-> SubGraph_State:
    logger.info("Executing text2sql generator agent.")
    try:
        input_query = state.banksql_queries
        query_str = str(input_query)
        customer_id = state.input_request.customer_id
        logger.info(f"Input query for Text2SQL Generator: {input_query}")
        logger.info(f"Customer ID for Text2SQL Generator: {customer_id}")

        # --- CACHE CHECK (SQLite) ---
        cache_key = _make_cache_key(customer_id, query_str)
        cached_sql = None
        if db_conn is not None:
            cached_sql = db_utils.get_cached_sql(db_conn, cache_key)
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

        state.agent_metrics["text2sql_generator"] = metric_metadata
        logger.info(f"state.agent_metrics updated in text2sql generator agent\n{state.agent_metrics}")

        # --- CACHE WRITE (SQLite) ---
        generated_sql = sql_generator.sql_query
        if db_conn is not None:
            try:
                db_utils.save_cached_sql(db_conn, cache_key, customer_id, query_str, generated_sql)
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
