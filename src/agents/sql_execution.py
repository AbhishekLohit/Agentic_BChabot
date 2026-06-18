import sqlite3
import re
from logging import Logger
from app_models import FallbackResponse, Graph_State, Text2SqlGeneratorResponse

# Hard ceiling on how many rows a single query may return to the user. Detail
# transaction lists are capped to the most recent MAX_RESULT_ROWS rows.
MAX_RESULT_ROWS = 20

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
                sql_query = sql_query_clean + f" LIMIT {MAX_RESULT_ROWS}"
            else:
                sql_query = sql_query_clean + f" ORDER BY transaction_date DESC LIMIT {MAX_RESULT_ROWS}"
            logger.info(f"Programmatic safeguard applied. Query rewritten to: {sql_query}")
        elif not has_aggregates and has_limit:
            # Clamp an explicitly generated LIMIT down to the ceiling, so requests
            # like "show all my transactions in 2026" can never exceed MAX_RESULT_ROWS.
            def _clamp_limit(match: re.Match) -> str:
                requested = int(match.group(1))
                return f"LIMIT {min(requested, MAX_RESULT_ROWS)}"
            new_query = re.sub(r'\blimit\s+(\d+)', _clamp_limit, sql_query, flags=re.IGNORECASE)
            if new_query != sql_query:
                sql_query = new_query
                logger.info(f"LIMIT clamped to {MAX_RESULT_ROWS}. Query rewritten to: {sql_query}")

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
            
            # Truncation fallback: final hard cap in Python
            if len(results) > MAX_RESULT_ROWS:
                logger.warning(f"Database returned {len(results)} rows. Truncating to {MAX_RESULT_ROWS}.")
                results = results[:MAX_RESULT_ROWS]
                results.append(f"[System Notification: Only the {MAX_RESULT_ROWS} most recent relevant transactions are displayed for brevity]")
            
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

    

