import sqlite3
import re
import threading
from contextlib import contextmanager
from logging import Logger
from app_models import FallbackResponse, SubGraph_State
from utils.chart_intent import is_explicit_chart_request
from langsmith import traceable


def _trace_rows(outputs: dict) -> dict:
    """Keep traces small: column names, row count and the first 5 rows only."""
    out = outputs.get("output") if isinstance(outputs, dict) and "output" in outputs else outputs
    columns, rows = out if isinstance(out, tuple) else (None, None)
    return {"columns": columns, "row_count": len(rows) if rows is not None else None,
            "rows_preview": [list(r) for r in (rows or [])[:5]]}


# Read-only guard for model-written SQL. The connection is shared with the chat,
# cache and dispute tables, so a generated query may only read these tables.
READABLE_TABLES = frozenset({"transactions"})
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),  # WITH RECURSIVE
})
_BLOCKED_FUNCTIONS = frozenset({"load_extension"})
# Prefixed to guarded SQL so sqlite3's statement cache never hands back a copy
# of the same text that app code prepared earlier without the guard (the
# authorizer only runs when a statement is prepared).
_GUARD_MARK = "/* read-only */ "


def _schema_objects(db_conn) -> set[str]:
    rows = db_conn.execute(
        "SELECT name FROM sqlite_master UNION SELECT name FROM sqlite_temp_master"
    ).fetchall()
    return {str(name).lower() for (name,) in rows}


@contextmanager
def _read_only(db_conn):
    """While the query is prepared, deny anything but reads of READABLE_TABLES.
    Reads of a CTE name (WITH RECURSIVE reports one) are allowed; reads of any
    other table, view or sqlite_* table are denied. Other threads using the
    shared connection are never affected. A denied statement raises
    sqlite3.DatabaseError ("not authorized"), which takes the normal failed-SQL
    path (sql_correction, then fallback)."""
    owner = threading.get_ident()
    blocked_names = _schema_objects(db_conn) - READABLE_TABLES

    def authorizer(action, arg1, arg2, _db, _trigger):
        if threading.get_ident() != owner:
            return sqlite3.SQLITE_OK
        if action not in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            name = (arg1 or "").lower()
            if name in blocked_names or name.startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db_conn.set_authorizer(authorizer)
    try:
        yield
    finally:
        db_conn.set_authorizer(None)


@traceable(run_type="tool", name="sql_query", process_outputs=_trace_rows)
def _execute_sql(cursor: sqlite3.Cursor, sql_query: str):
    """Run the query; (columns, rows) for a result set, (None, None) otherwise.
    A LangSmith tool span when tracing is on, a plain call when it is off."""
    cursor.execute(_GUARD_MARK + sql_query)
    if cursor.description is None:
        return None, None
    return [col[0] for col in cursor.description], cursor.fetchall()

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
        # The authorizer runs while the statement is prepared, so fetching the
        # rows inside _execute_sql is covered too.
        with _read_only(db_conn):
            columns, raw_rows = _execute_sql(cursor, sql_query)
        if columns is not None:
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
            # Nothing to commit: the read-only guard blocks every write.
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

    
