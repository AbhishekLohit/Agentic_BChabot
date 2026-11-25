"""Fixed, parameterised SQL checks used by the Disputes subagent.

Nothing here calls a model, and every query is scoped by a customer_id that the caller
takes from the signed-in request, never from model output.
"""
import re
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from langsmith import traceable


def _without_conn(inputs: dict) -> dict:
    """Trace inputs without the sqlite connection object."""
    return {k: v for k, v in inputs.items() if k != "conn"}


def _fetch_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    """Rows as plain dicts, whatever row_factory the connection uses."""
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _to_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def describe(txn: dict) -> str:
    return f"£{float(txn['amount_usd']):,.2f} to {txn['merchant_brand_name']} on {txn['transaction_date']}"


def get_transaction(conn: sqlite3.Connection, customer_id: str, transaction_id: str) -> dict | None:
    """The transaction, but only if it belongs to this customer."""
    rows = _fetch_all(
        conn,
        "SELECT * FROM transactions WHERE customer_id = ? AND transaction_id = ?",
        (customer_id, transaction_id),
    )
    return rows[0] if rows else None


def _normalise(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(name).lower()).split())


def merchant_score(said: str, actual: str) -> float:
    """How well the merchant name the customer gave matches a real one, from 0 to 1.

    Forgiving on purpose: case, punctuation and spacing are ignored, a name contained in the other
    ("Northern Hotel" in "Great Northern Hotel") or the initials ("GNH") count as a full match,
    and anything else is scored on spelling similarity so typos ("Grate Nothern") still match."""
    a, b = _normalise(said), _normalise(actual)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    initials = "".join(w[0] for w in b.split())
    if len(initials) > 1 and a.replace(" ", "") == initials:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


@traceable(run_type="tool", name="dispute_find_transactions", process_inputs=_without_conn)
def find_transactions(
    conn: sqlite3.Connection,
    customer_id: str,
    amount: float,
    on_date: date,
    merchant: str,
    amount_tolerance: float = 0.0,
    min_merchant_score: float = 0.6,
    limit: int = 3,
) -> list[dict]:
    """The customer's transactions that could be the payment they describe, best first.

    The date must match exactly. The amount may be up to `amount_tolerance` pounds out
    (inclusive), and the merchant has to match the name they gave at least `min_merchant_score`
    (see merchant_score). Results are ordered by how close the amount is, then how well the
    merchant matches, then transaction_id so the order is stable. Each row carries
    `amount_difference` and `merchant_score`.
    Every type of transaction is searched so that, for example, a direct debit gets a clear
    "cannot be disputed here" answer instead of looking like it does not exist."""
    rows = _fetch_all(
        conn,
        """
        SELECT * FROM transactions
        WHERE customer_id = ?
          AND substr(transaction_date, 1, 10) = ?
          AND ABS(amount_usd - ?) <= ?
        """,
        (customer_id, on_date.isoformat(), amount, amount_tolerance + 0.005),
    )
    matches = []
    for row in rows:
        score = merchant_score(merchant, row["merchant_brand_name"])
        if score >= min_merchant_score:
            row["merchant_score"] = round(score, 3)
            row["amount_difference"] = round(abs(float(row["amount_usd"]) - amount), 4)
            matches.append(row)
    matches.sort(key=lambda r: (r["amount_difference"], -r["merchant_score"], r["transaction_id"]))
    return matches[:limit]


def is_exact_match(row: dict) -> bool:
    """Same date (already guaranteed by the search), the amount to the penny, and the merchant.
    The data stores unrounded amounts, so anything that rounds to what the customer said counts."""
    return row["amount_difference"] <= 0.0051 and row["merchant_score"] >= 0.999


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

@dataclass
class Eligibility:
    ok: bool
    code: str       # ok | not_found | not_card_payment | outside_window | already_disputed
    message: str


_NOT_CARD_PAYMENT = {
    "income": "That is money coming into your account rather than a card payment, so it can't be raised as a card dispute.",
    "direct_debit": "That was paid by direct debit, which follows a different process from card disputes, so I can't raise it here.",
    "standing_order": "That is a standing order you set up yourself, so it isn't something I can raise as a card dispute.",
}


@traceable(run_type="tool", name="dispute_check_eligibility", process_inputs=_without_conn)
def check_eligibility(
    txn: dict | None,
    today: date,
    window_days: int,
    has_open_dispute: bool,
) -> Eligibility:
    if txn is None:
        return Eligibility(False, "not_found", "I couldn't find that transaction on your account.")
    if txn["transaction_type"] != "payment":
        msg = _NOT_CARD_PAYMENT.get(
            txn["transaction_type"],
            "That isn't a card payment, so I can't raise it as a card dispute.",
        )
        return Eligibility(False, "not_card_payment", msg)
    age_days = (today - _to_date(txn["transaction_date"])).days
    if age_days > window_days:
        return Eligibility(
            False,
            "outside_window",
            f"That payment was made on {txn['transaction_date']}, which is more than "
            f"{window_days} days ago, so it is outside the dispute window.",
        )
    if has_open_dispute:
        return Eligibility(False, "already_disputed", "You already have an open dispute for this transaction.")
    return Eligibility(True, "ok", "")


# ---------------------------------------------------------------------------
# Pre-checks
# ---------------------------------------------------------------------------

def detect_duplicate(
    conn: sqlite3.Connection,
    customer_id: str,
    txn: dict,
    window_days: int,
) -> list[dict]:
    """Other card payments to the same merchant for the same amount within `window_days`."""
    return _fetch_all(
        conn,
        """
        SELECT transaction_id, transaction_date, amount_usd
        FROM transactions
        WHERE customer_id = ?
          AND transaction_id <> ?
          AND transaction_type = 'payment'
          AND LOWER(merchant_brand_name) = LOWER(?)
          AND ABS(amount_usd - ?) < 0.01
          AND ABS(julianday(transaction_date) - julianday(?)) <= ?
        ORDER BY transaction_date
        """,
        (customer_id, txn["transaction_id"], txn["merchant_brand_name"],
         txn["amount_usd"], txn["transaction_date"], window_days),
    )


@dataclass
class RecurringInfo:
    prior_count: int
    is_recurring: bool       # paid this merchant at least `min_prior` times before
    monthly: bool            # ...and the payments follow a steady monthly rhythm
    last_date: str | None
    typical_amount: float | None


def detect_recurring(
    conn: sqlite3.Connection,
    customer_id: str,
    txn: dict,
    min_prior: int,
) -> RecurringInfo:
    prior = _fetch_all(
        conn,
        """
        SELECT transaction_date, amount_usd
        FROM transactions
        WHERE customer_id = ?
          AND transaction_id <> ?
          AND transaction_type <> 'income'
          AND LOWER(merchant_brand_name) = LOWER(?)
          AND transaction_date < ?
        ORDER BY transaction_date
        """,
        (customer_id, txn["transaction_id"], txn["merchant_brand_name"], txn["transaction_date"]),
    )
    if not prior:
        return RecurringInfo(0, False, False, None, None)

    amounts = [float(r["amount_usd"]) for r in prior]
    info = RecurringInfo(
        prior_count=len(prior),
        is_recurring=len(prior) >= min_prior,
        monthly=False,
        last_date=str(prior[-1]["transaction_date"])[:10],
        typical_amount=round(statistics.median(amounts), 2),
    )

    # Monthly rhythm: the last few gaps (including this payment) centre on ~a month and
    # the amounts stay close to this one. Needs at least three payments in total.
    recent = [_to_date(r["transaction_date"]) for r in prior[-6:]] + [_to_date(txn["transaction_date"])]
    gaps = [(b - a).days for a, b in zip(recent, recent[1:])]
    if len(gaps) >= 2:
        similar = sum(1 for a in amounts[-6:] if abs(a - float(txn["amount_usd"])) <= 0.1 * float(txn["amount_usd"]))
        if 26 <= statistics.median(gaps) <= 35 and similar >= max(2, len(amounts[-6:]) // 2):
            info.monthly = True
    return info


def find_refund_credit(
    conn: sqlite3.Connection,
    customer_id: str,
    txn: dict,
) -> dict | None:
    """An incoming credit from the same merchant on or after the payment date, for no
    more than the payment amount. The earliest one."""
    rows = _fetch_all(
        conn,
        """
        SELECT transaction_id, transaction_date, amount_usd
        FROM transactions
        WHERE customer_id = ?
          AND transaction_type = 'income'
          AND LOWER(merchant_brand_name) = LOWER(?)
          AND transaction_date >= ?
          AND amount_usd <= ? + 0.01
        ORDER BY transaction_date
        LIMIT 1
        """,
        (customer_id, txn["merchant_brand_name"], txn["transaction_date"], txn["amount_usd"]),
    )
    return rows[0] if rows else None


@dataclass
class RefundWait:
    too_early: bool
    days_since: int
    earliest_check: date


def refund_wait(txn: dict, today: date, wait_days: int) -> RefundWait:
    paid_on = _to_date(txn["transaction_date"])
    days_since = (today - paid_on).days
    return RefundWait(days_since < wait_days, days_since, paid_on + timedelta(days=wait_days))
