"""The SQL agent may only read the transactions table (agents.sql_execution._read_only)."""
import asyncio
import sqlite3
import threading
from unittest.mock import Mock

import pytest

from agents.sql_execution import _execute_sql, _read_only, execute_query
from app_models import SubGraph_State, Text2SqlGeneratorResponse


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.execute("CREATE TABLE transactions (transaction_id TEXT, customer_id TEXT, amount_usd REAL,"
              " transaction_date TEXT, merchant_brand_name TEXT)")
    c.executemany("INSERT INTO transactions VALUES (?,?,?,?,?)", [
        ("T1", "Cust_1", 10.0, "2026-10-01", "Tesco"),
        ("T2", "Cust_1", 25.5, "2026-10-02", "Netflix"),
    ])
    c.execute("CREATE TABLE chat_messages (id INTEGER, content TEXT)")
    c.execute("INSERT INTO chat_messages VALUES (1, 'hello')")
    c.commit()
    yield c
    c.close()


def _run(conn, sql):
    state = SubGraph_State(
        text2sql_generator_response=Text2SqlGeneratorResponse(user_query="q", sql_query=sql),
        database_query_results=None, fallback_response=None, banksql_queries=["q"],
        input_request=None, agent_metrics={},
    )
    return asyncio.run(execute_query(state, conn, Mock()))


@pytest.mark.parametrize("sql", [
    "SELECT SUM(amount_usd) AS total FROM transactions WHERE customer_id = 'Cust_1'",
    "SELECT merchant_brand_name, amount_usd FROM transactions ORDER BY amount_usd DESC LIMIT 5",
    "WITH m AS (SELECT merchant_brand_name AS n, SUM(amount_usd) AS s FROM transactions GROUP BY n)"
    " SELECT n, s FROM m ORDER BY s DESC",
    "SELECT COUNT(*) FROM transactions WHERE amount_usd > (SELECT AVG(amount_usd) FROM transactions)",
    "SELECT strftime('%Y-%m', transaction_date) AS ym, ROUND(SUM(amount_usd), 2) FROM transactions"
    " WHERE transaction_date >= date('now', '-10 years') GROUP BY ym",
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3)"
    " SELECT COUNT(*) FROM n, transactions",
    "SELECT SUM(amount_usd) FROM transactions;",
])
def test_reads_of_transactions_still_work(conn, sql):
    state = _run(conn, sql)
    assert state.fallback_response is None
    assert state.database_query_results


@pytest.mark.parametrize("sql", [
    "DELETE FROM transactions",
    "UPDATE transactions SET amount_usd = 0",
    "INSERT INTO transactions (transaction_id) VALUES ('X')",
    "DROP TABLE transactions",
    "CREATE TABLE evil (x)",
    "SELECT * FROM chat_messages",
    "SELECT t.amount_usd FROM transactions t JOIN chat_messages c ON 1 = 1",
    "SELECT name FROM sqlite_master",
    "SELECT COUNT(*) FROM sqlite_schema",
    "WITH x AS (SELECT content FROM chat_messages) SELECT COUNT(*) FROM x",
    "ATTACH DATABASE ':memory:' AS other",
    "PRAGMA table_info(transactions)",
    "SELECT 1; DELETE FROM transactions",
])
def test_everything_else_is_blocked_and_takes_the_failed_sql_path(conn, sql):
    state = _run(conn, sql)
    assert state.fallback_response is not None
    assert state.fallback_response.fallback_reason == "query_error"
    assert not state.database_query_results
    # nothing was changed
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 1


def test_guard_is_removed_after_the_query(conn):
    _run(conn, "SELECT * FROM chat_messages")          # blocked
    conn.execute("INSERT INTO chat_messages VALUES (2, 'app write')")   # app code still writes
    assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 2


def test_other_threads_are_not_restricted(conn):
    result = {}
    with _read_only(conn):
        def other():
            result["rows"] = conn.execute("SELECT content FROM chat_messages").fetchall()
        t = threading.Thread(target=other)
        t.start()
        t.join()
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT id FROM chat_messages")
    assert result["rows"] == [("hello",)]


def test_statement_cached_by_another_thread_does_not_bypass_the_guard(conn):
    """Another thread may prepare (and cache) the same text while the guard is on;
    the guarded copy must still be checked, not reused from the cache."""
    sql = "SELECT COUNT(*) FROM chat_messages"
    with _read_only(conn):
        t = threading.Thread(target=lambda: conn.execute(sql).fetchall())
        t.start()
        t.join()
        with pytest.raises(sqlite3.DatabaseError):
            _execute_sql(conn.cursor(), sql)
