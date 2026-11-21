import json
import sqlite3
import uuid
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

        -- Disputes subagent -------------------------------------------------
        -- One in-progress dispute per (session, customer); removed once filed,
        -- resolved, cancelled or handed off.
        CREATE TABLE IF NOT EXISTS dispute_drafts (
            session_id   TEXT NOT NULL,
            customer_id  TEXT NOT NULL,
            draft_json   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (session_id, customer_id)
        );

        CREATE TABLE IF NOT EXISTS disputes (
            case_id        TEXT PRIMARY KEY,
            customer_id    TEXT NOT NULL,
            session_id     TEXT,
            transaction_id TEXT NOT NULL,
            reason         TEXT NOT NULL,
            answers_json   TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'open',
            created_at     TEXT NOT NULL
        );
        -- Filing the same dispute twice returns the existing case instead of a duplicate.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_disputes_open_unique
            ON disputes(customer_id, transaction_id, reason) WHERE status = 'open';

        -- Audit trail: one row per step that matters (draft started, reason set,
        -- confirmed, filed, resolved, suspected fraud, handoff...).
        CREATE TABLE IF NOT EXISTS dispute_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id      TEXT,
            customer_id  TEXT NOT NULL,
            session_id   TEXT,
            event        TEXT NOT NULL,
            detail_json  TEXT,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dispute_events_case ON dispute_events(case_id);
    """)
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------

def get_dispute_draft(
    conn: sqlite3.Connection,
    customer_id: str,
    session_id: str,
    max_age_hours: int = 24,
) -> str | None:
    """Return the draft JSON for this session, or None when there is none or it has
    not been touched for `max_age_hours` (an abandoned flow must not hijack a later chat)."""
    cur = conn.execute(
        "SELECT draft_json, updated_at FROM dispute_drafts WHERE customer_id = ? AND session_id = ?",
        (customer_id, session_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    draft_json, updated_at = row[0], row[1]
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%S")
    if updated_at < cutoff:
        return None
    return draft_json


def save_dispute_draft(
    conn: sqlite3.Connection,
    customer_id: str,
    session_id: str,
    draft_json: str,
) -> None:
    conn.execute(
        """
        INSERT INTO dispute_drafts (session_id, customer_id, draft_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(session_id, customer_id) DO UPDATE SET
            draft_json = excluded.draft_json,
            updated_at = excluded.updated_at
        """,
        (session_id, customer_id, draft_json, now_iso()),
    )
    conn.commit()


def clear_dispute_draft(conn: sqlite3.Connection, customer_id: str, session_id: str) -> None:
    conn.execute(
        "DELETE FROM dispute_drafts WHERE customer_id = ? AND session_id = ?",
        (customer_id, session_id),
    )
    conn.commit()


def has_open_dispute(conn: sqlite3.Connection, customer_id: str, transaction_id: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM disputes WHERE customer_id = ? AND transaction_id = ? AND status = 'open' LIMIT 1",
        (customer_id, transaction_id),
    )
    return cur.fetchone() is not None


def add_dispute_event(
    conn: sqlite3.Connection,
    customer_id: str,
    session_id: str | None,
    event: str,
    detail: dict | None = None,
    case_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO dispute_events (case_id, customer_id, session_id, event, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (case_id, customer_id, session_id, event, json.dumps(detail, default=str) if detail else None, now_iso()),
    )
    conn.commit()


def create_dispute(
    conn: sqlite3.Connection,
    customer_id: str,
    session_id: str,
    transaction_id: str,
    reason: str,
    answers: dict,
) -> tuple[str, bool]:
    """File a dispute. Returns (case_id, created). If an open dispute already exists for
    the same customer + transaction + reason, that case is returned and created is False."""
    case_id = f"DSP-{uuid.uuid4().hex[:8].upper()}"
    try:
        conn.execute(
            """
            INSERT INTO disputes (case_id, customer_id, session_id, transaction_id, reason, answers_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (case_id, customer_id, session_id, transaction_id, reason, json.dumps(answers), now_iso()),
        )
        conn.commit()
        return case_id, True
    except sqlite3.IntegrityError:
        conn.rollback()
        cur = conn.execute(
            "SELECT case_id FROM disputes WHERE customer_id = ? AND transaction_id = ? AND reason = ? AND status = 'open'",
            (customer_id, transaction_id, reason),
        )
        existing = cur.fetchone()
        return (existing[0] if existing else case_id), False


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
