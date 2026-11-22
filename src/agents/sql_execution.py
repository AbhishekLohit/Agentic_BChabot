import sqlite3
import re
from logging import Logger
from app_models import FallbackResponse, SubGraph_State
from utils.chart_intent import is_explicit_chart_request

# Hard ceiling on how many rows a single query may return to the user. Detail
# transaction lists are capped to the most recent MAX_RESULT_ROWS rows.
MAX_RESULT_ROWS = 20
RESULT_TRUNCATION_PROBE = MAX_RESULT_ROWS + 1
MAX_CHART_RESULT_ROWS = 500
CHART_RESULT_TRUNCATION_PROBE = MAX_CHART_RESULT_ROWS + 1

async def execute_query(
    state: SubGraph_State,
    db_conn: any,
    logger: Logger
) -> SubGraph_State:
    """
    Execute the generated query and store rows as dictionaries in graph state.
    Includes safeguards to prevent fetching massive amounts of data.
    """
    logger.info("Executing SQL execution agent.")
    sql_query = state.text2sql_generator_response.sql_query.strip()

    chart_request = is_explicit_chart_request(
        state.input_request.user_query if state.input_request else None
    )
    is_grouped_aggregate = (
        re.search(r"\bgroup\s+by\b", sql_query, re.IGNORECASE) is not None
        and re.search(
            r"\b(count|sum|avg|min|max|group_concat)\s*\(",
            sql_query,
            re.IGNORECASE,
        )
        is not None
    )
    chart_summary = chart_request and is_grouped_aggregate

    # Programmatic Safeguard: Check if SELECT query lacks aggregates and LIMIT
    if sql_query.lower().startswith("select"):
        has_aggregates = re.search(r'\b(count|sum|avg|min|max|group_concat)\b\s*\(', sql_query, re.IGNORECASE) is not None
        has_limit = re.search(r'\blimit\b', sql_query, re.IGNORECASE) is not None
        if chart_summary:
            sql_query_clean = sql_query.rstrip(";").strip()
            if has_limit:
                sql_query = re.sub(
                    r"\blimit\s+(\d+)",
                    lambda match: (
                        f"LIMIT {min(int(match.group(1)), CHART_RESULT_TRUNCATION_PROBE)}"
                    ),
                    sql_query_clean,
                    count=1,
                    flags=re.IGNORECASE,
                )
            else:
                sql_query = (
                    f"{sql_query_clean} LIMIT {CHART_RESULT_TRUNCATION_PROBE}"
                )
            logger.info(
                "Applied chart-summary SQL result cap of %s rows.",
                CHART_RESULT_TRUNCATION_PROBE,
            )
        elif not has_aggregates and not has_limit:
            has_orderby = re.search(r'\border\s+by\b', sql_query, re.IGNORECASE) is not None
            sql_query_clean = sql_query.rstrip(";").strip()
            if has_orderby:
                sql_query = sql_query_clean + f" LIMIT {RESULT_TRUNCATION_PROBE}"
            else:
                sql_query = (
                    sql_query_clean
                    + f" ORDER BY transaction_date DESC LIMIT {RESULT_TRUNCATION_PROBE}"
                )
            logger.info(f"Programmatic safeguard applied. Query rewritten to: {sql_query}")
        elif not has_aggregates and has_limit:
            # Fetch one probe row beyond the response cap so truncation is detectable.
            def _clamp_limit(match: re.Match) -> str:
                requested = int(match.group(1))
                limit = (
                    RESULT_TRUNCATION_PROBE
                    if requested >= MAX_RESULT_ROWS
                    else requested
                )
                return f"LIMIT {limit}"
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
            columns = [col[0] for col in cursor.description]
            raw_rows = cursor.fetchall()
            result_limit = MAX_CHART_RESULT_ROWS if chart_summary else MAX_RESULT_ROWS
            state.database_results_truncated = len(raw_rows) > result_limit
            if state.database_results_truncated:
                logger.warning(
                    "Database returned %s rows. Truncating to %s.",
                    len(raw_rows),
                    result_limit,
                )
                raw_rows = raw_rows[:result_limit]

            results = [dict(zip(columns, row)) for row in raw_rows]
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

    
