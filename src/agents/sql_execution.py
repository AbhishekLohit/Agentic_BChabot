import sqlite3
import re
from logging import Logger
from app_models import FallbackResponse, Graph_State, Text2SqlGeneratorResponse

async def execute_query(
    state: Graph_State,
    db_conn: any,
    logger: Logger
) -> list[str]:
    """
    Execute the given SQL query and return results as a string.
    Includes safeguards to prevent fetching massive amounts of data.
    """
    logger.info("Executing SQL execution agent.")
    sql_query = state.text2sql_generator_response.sql_query.strip()
    
    # Programmatic Safeguard: Check if SELECT query lacks aggregates and LIMIT
    if sql_query.lower().startswith("select"):
        has_aggregates = re.search(r'\b(count|sum|avg|min|max|group_concat)\b\s*\(', sql_query, re.IGNORECASE) is not None
        has_limit = re.search(r'\blimit\b', sql_query, re.IGNORECASE) is not None
        if not has_aggregates and not has_limit:
            has_orderby = re.search(r'\border\s+by\b', sql_query, re.IGNORECASE) is not None
            sql_query_clean = sql_query.rstrip(";").strip()
            if has_orderby:
                sql_query = sql_query_clean + " LIMIT 50"
            else:
                sql_query = sql_query_clean + " ORDER BY transaction_date DESC LIMIT 50"
            logger.info(f"Programmatic safeguard applied. Query rewritten to: {sql_query}")

    if not sql_query:
        logger.warning("Empty query received in execute_query.")
        state.sql_query_correction = True
        return state

    cursor = db_conn.cursor()
    try:
        cursor.execute(sql_query)
        if cursor.description is not None:
            # Return results as list of dicts
            columns = [col[0] for col in cursor.description]
            raw_rows = cursor.fetchall()
            results = [str(dict(zip(columns, row))) for row in raw_rows]
            
            # Truncation fallback: limit to 50 results in Python
            if len(results) > 50:
                logger.warning(f"Database returned {len(results)} rows. Truncating to 50.")
                results = results[:50]
                results.append("[System Notification: Only the top 50 most recent relevant transactions are displayed for brevity]")
            
            state.database_query_results = results
            logger.info(f"SQL executed successfully. Results count: {len(results)}\n")
            
        else:
            db_conn.commit()
            logger.info(f"SQL execution completed with no output description (e.g. non-SELECT query or empty execution).\n")
    except sqlite3.Error as e:
        logger.warning(f"SQL Error encountered — routing to correction: {e}")
        state.fallback_response = FallbackResponse(
            fallback_flag=True,
            fallback_node="sql_execution",
            fallback_reason="query_error",
            fallback_message="The generated SQL query failed. Attempting automatic correction."
        )
    finally:
        cursor.close()
    return state

    

