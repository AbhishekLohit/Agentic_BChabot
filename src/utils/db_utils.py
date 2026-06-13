import sqlite3
from datetime import datetime, timezone, timedelta


def setup_chat_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_sessions (
            session_id   TEXT PRIMARY KEY,
            customer_id  TEXT NOT NULL,
            summary      TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            customer_id  TEXT NOT NULL,
            role         TEXT NOT NULL,
            content      TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);

        CREATE TABLE IF NOT EXISTS request_metrics (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id           TEXT NOT NULL,
            session_id            TEXT NOT NULL,
            agent_name            TEXT NOT NULL,
            model_used            TEXT,
            input_tokens          INTEGER,
            output_tokens         INTEGER,
            total_tokens          INTEGER,
            cached_tokens         INTEGER,
            cache_creation_tokens INTEGER,
            latency               REAL,
            cost_without_cache    REAL,
            cost_with_cache       REAL,
            created_at            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_customer ON request_metrics(customer_id);

        CREATE TABLE IF NOT EXISTS query_cache (
            cache_key    TEXT PRIMARY KEY,
            customer_id  TEXT NOT NULL,
            nl_query     TEXT NOT NULL,
            sql_query    TEXT NOT NULL,
            hit_count    INTEGER DEFAULT 0,
            created_at   TEXT NOT NULL,
            last_used_at TEXT NOT NULL
        );
    """)
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def upsert_session(
    conn: sqlite3.Connection,
    session_id: str,
    customer_id: str,
    summary: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_sessions (session_id, customer_id, summary, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (session_id, customer_id, summary, updated_at, updated_at),
    )
    conn.commit()


def save_message(
    conn: sqlite3.Connection,
    session_id: str,
    customer_id: str,
    role: str,
    content: str,
    created_at: str,
) -> None:
    conn.execute(
        "INSERT INTO chat_messages (session_id, customer_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, customer_id, role, content, created_at),
    )
    conn.commit()


def get_sessions(conn: sqlite3.Connection, customer_id: str) -> list[dict]:
    cursor = conn.execute(
        "SELECT session_id, summary, created_at, updated_at FROM chat_sessions WHERE customer_id = ? ORDER BY updated_at DESC",
        (customer_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_messages(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    cursor = conn.execute(
        "SELECT role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def delete_other_sessions(
    conn: sqlite3.Connection,
    customer_id: str,
    keep_session_id: str,
) -> int:
    """
    Delete all of a customer's chat sessions (and their messages) EXCEPT the one
    given by keep_session_id. Returns the number of sessions removed.
    """
    cur = conn.execute(
        "DELETE FROM chat_sessions WHERE customer_id = ? AND session_id != ?",
        (customer_id, keep_session_id),
    )
    deleted = cur.rowcount
    conn.execute(
        "DELETE FROM chat_messages WHERE customer_id = ? AND session_id != ?",
        (customer_id, keep_session_id),
    )
    conn.commit()
    return deleted


def get_recent_messages(
    conn: sqlite3.Connection,
    customer_id: str,
    hours: int = 24,
    exclude_session_id: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """
    Return a customer's chat messages from the last `hours` (rolling window),
    in chronological order. Optionally excludes one session (e.g. the current one)
    and keeps at most the `limit` most recent messages.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    params: list = [customer_id, cutoff]
    sql = (
        "SELECT role, content, created_at FROM chat_messages "
        "WHERE customer_id = ? AND created_at >= ?"
    )
    if exclude_session_id:
        sql += " AND session_id != ?"
        params.append(exclude_session_id)
    sql += " ORDER BY id ASC"

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if limit and len(rows) > limit:
        rows = rows[-limit:]  # keep the most recent, preserve chronological order
    return rows


def get_cached_sql(conn: sqlite3.Connection, cache_key: str) -> str | None:
    row = conn.execute(
        "SELECT sql_query FROM query_cache WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE query_cache SET hit_count = hit_count + 1, last_used_at = ? WHERE cache_key = ?",
            (now_iso(), cache_key),
        )
        conn.commit()
        return row["sql_query"]
    return None


def save_cached_sql(
    conn: sqlite3.Connection,
    cache_key: str,
    customer_id: str,
    nl_query: str,
    sql_query: str,
) -> None:
    conn.execute(
        """INSERT INTO query_cache (cache_key, customer_id, nl_query, sql_query, created_at, last_used_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(cache_key) DO UPDATE SET
             sql_query = excluded.sql_query,
             last_used_at = excluded.last_used_at,
             hit_count = hit_count + 1""",
        (cache_key, customer_id, nl_query, sql_query, now_iso(), now_iso()),
    )
    conn.commit()


def save_metrics(
    conn: sqlite3.Connection,
    customer_id: str,
    session_id: str,
    agent_metrics: dict,
    created_at: str,
) -> None:
    rows = [
        (
            customer_id,
            session_id,
            agent_name,
            m.model_used,
            m.input_tokens,
            m.output_tokens,
            m.total_tokens,
            m.cached_tokens,
            m.cache_creation_tokens,
            m.latency,
            m.model_cost_without_cache,
            m.model_cost_with_cache,
            created_at,
        )
        for agent_name, m in agent_metrics.items()
    ]
    conn.executemany(
        """
        INSERT INTO request_metrics
        (customer_id, session_id, agent_name, model_used, input_tokens, output_tokens,
         total_tokens, cached_tokens, cache_creation_tokens, latency,
         cost_without_cache, cost_with_cache, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
