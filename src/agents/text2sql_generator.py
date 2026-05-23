from email import message
from logging import Logger
import time
from app_models import AgentMetrics, FallbackResponse, Graph_State, SubGraph_State,Text2SqlGeneratorResponse
from typing import Any
import sqlite3

from utils import agent_utils

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
)-> SubGraph_State:
    logger.info("Executing text2sql generator agent.")
    # Render the final prompt string using .format()
    try:
        input_query = state.banksql_queries
        logger.info(f"Input query for Text2SQL Generator: {input_query}")
        logger.info(f"Customer ID for Text2SQL Generator: {state.input_request.customer_id}")
        # final_prompt = prompt.format(
        #                         input_query=input_query,
        #                         customer_id=state.input_request.customer_id
        # )
        messages = [
            {"role": "system", "content": [{"type": "text", "text": prompt.template, "cache_control": {"type": "ephemeral","ttl": "1h"}}]},
            {"role": "user", "content": f"""
                                input_query: {input_query}
                                customer_id: {state.input_request.customer_id}"""}
        ]
        structured_llm = llm.with_structured_output(Text2SqlGeneratorResponse,include_raw=True)
        # Send the prompt to the model directly (no chain)
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

        # ✅ Update state
        # state.agent_metrics = AgentMetrics(
        #     context_builder_metrics=None,
        #     suprivisor_metrics=None,
        #     classification_metrics=None,
        #     text2sql_generator_metrics=metric_metadata,
        #     response_generator_metrics=None,
        #     sql_corection_metrics=None
        # )
        # ✅ FIX: Store in dict
        # if state.agent_metrics is None:
        #     state.agent_metrics = {}
        state.agent_metrics["text2sql_generator"] = metric_metadata

        logger.info(f"state.agent_metrics updated in text2sql generator agent\n{state.agent_metrics}")

        # ## Execute the generated SQL quer
        # sql_query = sql_generator.sql_query
        # query_results = execute_query(
        #     sql_query,
        #     db_conn=db_conn,
        #     logger=logger
        # )
        # if query_results[0] == "wrong_query":
        #     logger.info("The generated SQL query is wrong and needs to be corrected.")
        #     state.sql_query_correction = True
        #     return state
            
        # state.database_query_results = query_results
        # logger.info(f"Database query results: {query_results}\n")
    except Exception as e:
        logger.exception(f"Error in text2sql generator agent: {str(e)}")
        fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="text2sql_generator",
            fallback_reason="exception",
            fallback_message="exception in text2sql generator agent"
        )
        state.fallback_response = fallback_response
    return state 