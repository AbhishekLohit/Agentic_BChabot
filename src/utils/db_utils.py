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

        -- Readable details of each raised case: the payment as the bank recorded it, the
        -- reason, and every question asked with the customer's answer. One row per case.
        CREATE TABLE IF NOT EXISTS dispute_details (
            case_id               TEXT PRIMARY KEY REFERENCES disputes(case_id),
            customer_id           TEXT NOT NULL,
            session_id            TEXT,
            transaction_id        TEXT NOT NULL,
            card_number           TEXT,
            merchant              TEXT,
            merchant_category     TEXT,
            amount                REAL,
            transaction_date      TEXT,
            transaction_type      TEXT,
            reason                TEXT NOT NULL,
            reason_label          TEXT,
            visa_code             TEXT,
            dispute_for           TEXT,   -- one line: what the dispute is about
            what_happened         TEXT,
            merchant_contacted    TEXT,
            evidence              TEXT,
            questions_and_answers TEXT,   -- readable "Q: ... / A: ..." lines
            answers_json          TEXT,   -- [{"slot_id", "question", "answer"}, ...]
            status                TEXT NOT NULL DEFAULT 'open',
            created_at            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dispute_details_customer ON dispute_details(customer_id);

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
    # Columns added after a table first shipped (CREATE TABLE IF NOT EXISTS won't add them).
    columns = {row[1] for row in conn.execute("PRAGMA table_info(dispute_details)")}
    if "dispute_for" not in columns:
        conn.execute("ALTER TABLE dispute_details ADD COLUMN dispute_for TEXT")
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


def save_dispute_details(
    conn: sqlite3.Connection,
    case_id: str,
    customer_id: str,
    session_id: str,
    txn: dict,
    reason: str,
    reason_label: str,
    visa_code: str,
    qa: list[dict],
) -> None:
    """Store the readable details of a newly raised case. `qa` is the list of
    {"slot_id", "question", "answer"} in the order the questions were asked."""
    by_slot = {item["slot_id"]: item.get("answer") for item in qa}
    dispute_for = describe_dispute(reason_label, txn, by_slot.get("description"))
    conn.execute(
        """
        INSERT OR IGNORE INTO dispute_details (
            case_id, customer_id, session_id, transaction_id, card_number, merchant, merchant_category,
            amount, transaction_date, transaction_type, reason, reason_label, visa_code, dispute_for,
            what_happened, merchant_contacted, evidence, questions_and_answers, answers_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            case_id, customer_id, session_id, txn.get("transaction_id"),
            str(txn["card_number"]) if txn.get("card_number") is not None else None,
            txn.get("merchant_brand_name"), txn.get("merchant_system_category1_name"),
            round(float(txn["amount_usd"]), 2) if txn.get("amount_usd") is not None else None,
            txn.get("transaction_date"), txn.get("transaction_type"),
            reason, reason_label, visa_code, dispute_for,
            by_slot.get("description"), by_slot.get("contacted_merchant"), by_slot.get("evidence"),
            "\n".join(f"Q: {i['question']}\nA: {i.get('answer') or '(not answered)'}" for i in qa),
            json.dumps(qa),
            now_iso(),
        ),
    )
    conn.commit()


def describe_dispute(reason_label: str | None, txn: dict, what_happened: str | None) -> str:
    """One line saying what a dispute is about, e.g.
    "Goods or services not received: £156.37 to Topshop on 2026-06-02. My order never arrived." """
    amount = txn.get("amount_usd")
    payment = (
        f"£{float(amount):,.2f} to {txn.get('merchant_brand_name')} on {txn.get('transaction_date')}"
        if amount is not None else f"payment {txn.get('transaction_id')}"
    )
    text = f"{reason_label or 'Dispute'}: {payment}."
    if what_happened:
        text += f" {what_happened.strip()}"
    return text


def list_customer_disputes(
    conn: sqlite3.Connection, customer_id: str, limit: int = 20, case_ids: list[str] | None = None,
) -> list[dict]:
    """The customer's raised cases, newest first, with the status read live from `disputes`.
    Only the signed-in customer's rows: customer_id always comes from the request.
    With case_ids, only those cases (still only if they belong to this customer)."""
    id_filter = ""
    params: list = [customer_id]
    if case_ids:
        id_filter = f" AND d.case_id IN ({', '.join('?' for _ in case_ids)})"
        params += [c.upper() for c in case_ids]
    cur = conn.execute(
        f"""
        SELECT d.case_id, d.status, d.created_at, d.reason,
               x.merchant, x.amount, x.transaction_date, x.reason_label, x.dispute_for, x.what_happened,
               x.merchant_category, x.card_number, x.merchant_contacted, x.evidence
        FROM disputes d
        LEFT JOIN dispute_details x ON x.case_id = d.case_id
        WHERE d.customer_id = ?{id_filter}
        ORDER BY d.created_at DESC
        LIMIT ?
        """,
        (*params, limit),
    )
    names = [c[0] for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


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
