import os
import sqlite3
import sys
from datetime import date

import pytest

# The app runs with src/ on the import path (python src/app.py); do the same here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from agents.disputes.registry import load_dispute_registry  # noqa: E402
from app_setting import AppSettings  # noqa: E402
from utils import db_utils  # noqa: E402

TODAY = date(2026, 11, 10)

_TRANSACTIONS_DDL = """
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    card_number INTEGER,
    transaction_date TEXT,
    year INTEGER,
    month INTEGER,
    day INTEGER,
    amount_usd REAL,
    transaction_type TEXT,
    balance REAL,
    merchant_brand_name TEXT,
    merchant_system_category1_name TEXT,
    merchant_system_category2_name TEXT
)
"""


@pytest.fixture
def db():
    """In-memory copy of the app's schema, with the same Row factory the app uses."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(_TRANSACTIONS_DDL)
    db_utils.setup_chat_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def add_txn(db):
    def _add(transaction_id, txn_date, amount, merchant="Acme", txn_type="payment", customer="C1"):
        y, m, d = (int(p) for p in txn_date.split("-"))
        db.execute(
            "INSERT INTO transactions (transaction_id, customer_id, card_number, transaction_date, year, month, day,"
            " amount_usd, transaction_type, balance, merchant_brand_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (transaction_id, customer, 1234, txn_date, y, m, d, amount, txn_type, 1000.0, merchant),
        )
        db.commit()
    return _add


@pytest.fixture(scope="session")
def registry():
    return load_dispute_registry("dispute_types.yml")


@pytest.fixture
def settings():
    return AppSettings()


def events(db):
    return [r[0] for r in db.execute("SELECT event FROM dispute_events ORDER BY id").fetchall()]
