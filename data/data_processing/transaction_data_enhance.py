#!/usr/bin/env python3
"""
transaction_data_enhance.py

Enhance the banking-transaction dataset so the chatbot can answer a wider set of
questions: income vs outgoing, direct debits, standing orders, and running balance.

Two INDEPENDENT operations are exposed (so the expensive reshape is not re-run every
time you just want fresh dates):

  build  - one-time transform. Reads df_transaction_data_preprocessed.xlsx, drops the
           low-value columns (country, card_type, card_present, channel), tags every row
           with a transaction_type, injects synthetic income / recurring rows, computes a
           running balance, and writes df_transaction_data_enhanced.xlsx. The original
           preprocessed file is NEVER modified.

  shift  - repeatable step. Re-bases all dates in the enhanced file so the latest
           transaction lands on today's date (recomputing year/month/day). The running
           balance is preserved because a uniform shift keeps row ordering intact.

Usage:
    python transaction_data_enhance.py build
    python transaction_data_enhance.py shift
    python transaction_data_enhance.py build --input <xlsx> --output <xlsx>
    python transaction_data_enhance.py shift --input <xlsx>            # in place
    python transaction_data_enhance.py shift --input <xlsx> --output <xlsx>
"""

import os
import argparse
import uuid

import numpy as np
import pandas as pd

# --- Paths (relative to this file, so it runs from anywhere) ---
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(HERE, "df_transaction_data_preprocessed.xlsx")
DEFAULT_ENHANCED = os.path.join(HERE, "df_transaction_data_enhanced.xlsx")

# --- Tunable constants ---
OPENING_BALANCE = 20_000.0      # account balance just before the earliest transaction
MONTHLY_SALARY = 7_800.0        # recurring salary deposit (~ monthly outflow, so balance is stable)
SALARY_DAY = 28                 # day-of-month salary lands on
SEED = 42                       # reproducible synthetic data

# transaction_type categories that reduce the balance (income is the only one that adds)
OUTGOING_TYPES = ("payment", "direct_debit", "standing_order")

# Columns dropped from the preprocessed dataset
DROP_COLUMNS = ["country", "card_type", "card_present", "channel"]

# Final column order of the enhanced dataset
FINAL_COLUMNS = [
    "transaction_id", "customer_id", "card_number",
    "transaction_date", "year", "month", "day",
    "amount_usd", "transaction_type", "balance",
    "merchant_brand_name",
    "merchant_system_category1_name", "merchant_system_category2_name",
]


def _new_txn_id() -> str:
    """Generate a transaction id matching the existing 'TX_<8 hex>' shape."""
    return f"TX_{uuid.uuid4().hex[:8]}"


def _assign_existing_types(df: pd.DataFrame) -> pd.Series:
    """Heuristic transaction_type for the original (all-outgoing) rows."""
    cat1 = df["merchant_system_category1_name"].astype(str).str.lower()
    types = pd.Series("payment", index=df.index)
    types[cat1 == "bills"] = "direct_debit"
    types[cat1 == "charity"] = "standing_order"
    return types


def _row_dates(start, end):
    """Month-start anchor dates between the first and last day of the data span."""
    return pd.date_range(start=start.normalize(), end=end.normalize(), freq="MS")


def _make_row(date, amount, txn_type, brand, cat1, cat2, customer_id, card_number):
    date = pd.Timestamp(date).normalize()
    return {
        "transaction_id": _new_txn_id(),
        "customer_id": customer_id,
        "card_number": card_number,
        "transaction_date": date,
        "year": int(date.year),
        "month": int(date.month),
        "day": int(date.day),
        "amount_usd": round(float(amount), 6),
        "transaction_type": txn_type,
        "merchant_brand_name": brand,
        "merchant_system_category1_name": cat1,
        "merchant_system_category2_name": cat2,
    }


def _generate_synthetic_rows(df: pd.DataFrame, rng: np.random.Generator) -> list[dict]:
    """Build income + recurring outgoing rows spanning the same date range as `df`."""
    customer_id = df["customer_id"].mode().iloc[0]
    card_number = df["card_number"].mode().iloc[0]
    start, end = df["transaction_date"].min(), df["transaction_date"].max()
    months = _row_dates(start, end)
    rows: list[dict] = []

    # --- Income: monthly salary ---
    for m in months:
        pay_date = min(m.replace(day=SALARY_DAY), end)
        amount = MONTHLY_SALARY * rng.uniform(0.98, 1.02)  # tiny variation
        rows.append(_make_row(pay_date, amount, "income",
                              "Employer Payroll", "Income", "Salary",
                              customer_id, card_number))

    # --- Income: occasional refunds (tied to real merchants/categories) ---
    payment_rows = df[df["transaction_type"] == "payment"]
    n_refunds = max(1, len(months) // 2)
    if len(payment_rows):
        for _ in range(n_refunds):
            src = payment_rows.iloc[rng.integers(0, len(payment_rows))]
            date = pd.Timestamp(src["transaction_date"]) + pd.Timedelta(days=int(rng.integers(2, 20)))
            date = min(date, end)
            amount = float(src["amount_usd"]) * rng.uniform(0.5, 1.0)
            rows.append(_make_row(date, amount, "income",
                                  str(src["merchant_brand_name"]), "Income", "Refund",
                                  customer_id, card_number))

    # --- Income: incoming transfers from named people ---
    people = ["John Smith", "Emma Brown", "Raj Patel", "Sofia Garcia", "Liam Wilson"]
    n_transfers = max(1, len(months) // 3)
    for _ in range(n_transfers):
        m = months[rng.integers(0, len(months))]
        date = min(m.replace(day=int(rng.integers(1, 28))), end)
        amount = rng.uniform(50, 600)
        person = people[rng.integers(0, len(people))]
        rows.append(_make_row(date, amount, "income",
                              person, "Income", "Transfer In",
                              customer_id, card_number))

    # --- Recurring standing orders (user-initiated fixed transfers) ---
    standing_orders = [
        ("Rent", "Standing Order", "Rent", 1, 1200.0),
        ("Monthly Savings Transfer", "Standing Order", "Savings", 5, 300.0),
    ]
    for brand, cat1, cat2, dom, amount in standing_orders:
        for m in months:
            date = min(m.replace(day=dom), end)
            rows.append(_make_row(date, amount, "standing_order",
                                  brand, cat1, cat2, customer_id, card_number))

    # --- Recurring direct debits (subscriptions / utilities) ---
    direct_debits = [
        ("Netflix", "Bills", "Subscription", 15, 12.99),
        ("British Gas", "Bills", "Utilities", 18, 85.0),
        ("Vodafone", "Bills", "Phone", 22, 30.0),
    ]
    for brand, cat1, cat2, dom, amount in direct_debits:
        for m in months:
            date = min(m.replace(day=dom), end)
            rows.append(_make_row(date, amount, "direct_debit",
                                  brand, cat1, cat2, customer_id, card_number))

    return rows


def _compute_balance(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by date and compute a running balance (income adds, others subtract)."""
    df = df.sort_values(["transaction_date", "transaction_id"], kind="stable").reset_index(drop=True)
    signed = np.where(df["transaction_type"] == "income",
                      df["amount_usd"], -df["amount_usd"])
    df["balance"] = (OPENING_BALANCE + np.cumsum(signed)).round(2)
    return df


def build_enhanced(input_path: str = DEFAULT_INPUT, output_path: str = DEFAULT_ENHANCED) -> str:
    """One-time transform. Reads the preprocessed Excel, writes the enhanced Excel."""
    print(f"Reading preprocessed data: {input_path}")
    df = pd.read_excel(input_path)
    print(f"  Rows loaded: {len(df)}")

    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns])

    # Tag the original (outgoing) rows.
    df["transaction_type"] = _assign_existing_types(df)
    print("  Existing-row type counts:")
    for t, n in df["transaction_type"].value_counts().items():
        print(f"    {t}: {n}")

    # Inject synthetic income + recurring rows.
    rng = np.random.default_rng(SEED)
    synthetic = _generate_synthetic_rows(df, rng)
    syn_df = pd.DataFrame(synthetic)
    print(f"  Synthetic rows added: {len(syn_df)}")

    combined = pd.concat([df, syn_df], ignore_index=True)
    combined["transaction_date"] = pd.to_datetime(combined["transaction_date"])

    # Running balance.
    combined = _compute_balance(combined)

    # Final column order.
    combined = combined[FINAL_COLUMNS]
    combined["transaction_date"] = combined["transaction_date"].dt.strftime("%Y-%m-%d")

    combined.to_excel(output_path, index=False)
    print(f"\nWrote enhanced data: {output_path}")
    print(f"  Total rows: {len(combined)}")
    print(f"  transaction_type counts:\n{combined['transaction_type'].value_counts().to_string()}")
    print(f"  Final balance: {combined['balance'].iloc[-1]:,.2f}")
    return output_path


def shift_dates_to_today(path: str = DEFAULT_ENHANCED, output_path: str | None = None) -> str:
    """Re-base all dates so the latest transaction is today; recompute year/month/day."""
    output_path = output_path or path
    print(f"Reading enhanced data: {path}")
    df = pd.read_excel(path)

    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    today = pd.Timestamp.today().normalize()
    delta = today - df["transaction_date"].max()
    print(f"  Shifting all dates by {delta.days} days (latest -> {today.date()})")

    df["transaction_date"] = df["transaction_date"] + delta
    df["year"] = df["transaction_date"].dt.year
    df["month"] = df["transaction_date"].dt.month
    df["day"] = df["transaction_date"].dt.day
    df["transaction_date"] = df["transaction_date"].dt.strftime("%Y-%m-%d")

    df.to_excel(output_path, index=False)
    print(f"Wrote: {output_path}  (balance left unchanged; row order preserved)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="One-time enhancement transform.")
    p_build.add_argument("--input", default=DEFAULT_INPUT, help="Preprocessed Excel path")
    p_build.add_argument("--output", default=DEFAULT_ENHANCED, help="Enhanced Excel output path")

    p_shift = sub.add_parser("shift", help="Re-base dates so the latest is today.")
    p_shift.add_argument("--input", default=DEFAULT_ENHANCED, help="Enhanced Excel path")
    p_shift.add_argument("--output", default=None, help="Output path (defaults to in-place)")

    args = parser.parse_args()
    if args.command == "build":
        build_enhanced(args.input, args.output)
    elif args.command == "shift":
        shift_dates_to_today(args.input, args.output)


if __name__ == "__main__":
    main()
