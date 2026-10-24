#!/usr/bin/env python3
"""
build_database.py

Build a SQLite DB (transactions table) from the preprocessed transaction Excel file.

Usage:
    python build_database.py
    python build_database.py --db transactions.db --xlsx data/data_processing/df_transaction_data_preprocessed.xlsx
"""

import os
import sqlite3
import argparse
import pandas as pd
from datetime import datetime

DEFAULT_DB = "transactions.db"
DEFAULT_TRANSACTIONS_XLSX = "data/data_processing/df_transaction_data_enhanced.xlsx"


def build_database(db_path: str, transactions_xlsx: str, backup_old_db: bool = True):
    start_ts = datetime.now()
    print(f"\nStarting DB build at {start_ts.isoformat()}")
    print(f"Transactions Excel: {transactions_xlsx}")
    print(f"Output DB: {db_path}\n")

    if os.path.exists(db_path):
        if backup_old_db:
            backup_name = f"{db_path}.backup.{start_ts.strftime('%Y%m%d%H%M%S')}"
            os.replace(db_path, backup_name)
            print(f"Existing DB moved to backup: {backup_name}")
        else:
            os.remove(db_path)
            print("Existing DB removed")

    print("Loading Excel file...")
    df = pd.read_excel(transactions_xlsx)
    print(f"  Rows loaded: {len(df)}")

    # Normalize string columns
    df["transaction_id"] = df["transaction_id"].astype(str).str.strip()
    df["customer_id"] = df["customer_id"].astype(str).str.strip()
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce").dt.strftime("%Y-%m-%d")

    for col in ["transaction_type", "merchant_brand_name",
                "merchant_system_category1_name", "merchant_system_category2_name"]:
        df[col] = df[col].astype(str).str.strip()

    # Deduplicate by transaction_id
    df = df.drop_duplicates(subset=["transaction_id"], keep="first").reset_index(drop=True)
    print(f"  After dedup: {len(df)} rows")

    print("Creating SQLite DB and table...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript("""
    PRAGMA foreign_keys = OFF;

    CREATE TABLE IF NOT EXISTS transactions (
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
    );

    PRAGMA foreign_keys = ON;
    """)
    conn.commit()

    print("Inserting transactions...")
    cols = [
        "transaction_id", "customer_id", "card_number", "transaction_date",
        "year", "month", "day", "amount_usd", "transaction_type", "balance",
        "merchant_brand_name", "merchant_system_category1_name",
        "merchant_system_category2_name"
    ]
    df[cols].to_sql("transactions", conn, if_exists="append", index=False)
    print(f"  Inserted: {len(df)} rows")

    print("Creating indexes...")
    cur.executescript("""
        CREATE INDEX IF NOT EXISTS idx_transactions_customer_id ON transactions(customer_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);
        CREATE INDEX IF NOT EXISTS idx_transactions_category1 ON transactions(merchant_system_category1_name);
        CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions(merchant_brand_name);
        CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type);
    """)
    conn.commit()
    conn.close()

    elapsed = datetime.now() - start_ts
    print(f"\nDatabase build completed in {elapsed}.")
    print(f"  transactions: {len(df)}")
    print("\nDone.\n")


def main():
    parser = argparse.ArgumentParser(description="Build SQLite transactions DB from Excel.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Output SQLite DB path")
    parser.add_argument("--xlsx", default=DEFAULT_TRANSACTIONS_XLSX,
                        help="Preprocessed transactions Excel path")
    args = parser.parse_args()
    build_database(db_path=args.db, transactions_xlsx=args.xlsx, backup_old_db=True)


if __name__ == "__main__":
    main()
