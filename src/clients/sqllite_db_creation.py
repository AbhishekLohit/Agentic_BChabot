#!/usr/bin/env python3
"""
build_database_optimized.py

Build a fresh, normalized SQLite DB (restaurants, orders, order_items)
from CSV files. Performs validation, deduplication, and saves a cleaned
order_items CSV when invalid foreign keys are found.

Usage:
    python build_database_optimized.py
    # or override defaults:
    python build_database_optimized.py --orders data/orders.csv --items data/items.csv --db orders.db
"""

import os
import sqlite3
import argparse
import pandas as pd
from datetime import datetime

# Default paths (change if needed)
DEFAULT_DB = "orders_mar26.db"
DEFAULT_ORDERS_CSV = "data/data_files/df_order_table_modified.csv"
DEFAULT_ITEMS_CSV = "data/data_files/df_order_items_table_modified.csv"
# Uploaded file path from your session (kept here as a reference)
MENU_FILE_PATH = "/mnt/data/menu items.txt"

# --- Helper utilities -----------------------------------------------------

def normalize_str_col(df: pd.DataFrame, col: str):
    """Ensure column exists, fillna, cast to str and strip."""
    if col not in df.columns:
        df[col] = ""
    df[col] = df[col].astype(str).fillna("").str.strip()
    return df

def safe_cast_series_to_int(series, default=None):
    """Try to convert a Series to integer where possible, otherwise default."""
    def try_int(x):
        try:
            if pd.isna(x) or x == "":
                return default
            return int(float(x))
        except Exception:
            return default
    return series.apply(try_int)

def safe_cast_series_to_float(series, default=None):
    def try_float(x):
        try:
            if pd.isna(x) or x == "":
                return default
            return float(x)
        except Exception:
            return default
    return series.apply(try_float)

def make_restaurant_key(df, name_col="restaurant_name", addr_col="restaurant_address", city_col="restaurant_city"):
    # Create a composite key; use lowercased normalized values to reduce duplicates
    df = normalize_str_col(df, name_col)
    df = normalize_str_col(df, addr_col)
    df = normalize_str_col(df, city_col)
    return (df[name_col].str.lower() + "||" + df[addr_col].str.lower() + "||" + df[city_col].str.lower()).astype(str)

# --- Main building function ----------------------------------------------

def build_database(db_path: str, orders_csv: str, items_csv: str, cleaned_items_out: str = None, backup_old_db: bool = True):
    start_ts = datetime.now()
    print(f"\nStarting DB build at {start_ts.isoformat()}")
    print(f"Orders CSV: {orders_csv}")
    print(f"Items CSV: {items_csv}")
    print(f"Output DB: {db_path}")
    if cleaned_items_out:
        print(f"Cleaned items CSV will be saved to: {cleaned_items_out}")
    print(f"(Reference menu file: {MENU_FILE_PATH})\n")

    # 1) Optionally remove / backup old DB
    if os.path.exists(db_path):
        if backup_old_db:
            backup_name = f"{db_path}.backup.{start_ts.strftime('%Y%m%d%H%M%S')}"
            os.replace(db_path, backup_name)
            print(f"Existing DB moved to backup: {backup_name}")
        else:
            os.remove(db_path)
            print("Existing DB removed")

    # 2) Read CSVs
    print("Loading CSV files...")
    df_orders = pd.read_csv(orders_csv)
    df_items = pd.read_csv(items_csv)
    print(f"  Orders rows: {len(df_orders)}")
    print(f"  Order items rows: {len(df_items)}")

    # 3) Basic cleaning & normalization
    # Trim and ensure order_id columns present
    df_orders = df_orders.copy()
    df_items = df_items.copy()

    df_orders = df_orders.drop(columns=['order_date_old'])

    df_orders = normalize_str_col(df_orders, "order_id")
    df_items = normalize_str_col(df_items, "order_id")

    # Normalize restaurant fields
    for col in ["restaurant_name", "restaurant_address", "restaurant_city", "primary_cuisine", "restaurant_type"]:
        df_orders = normalize_str_col(df_orders, col)

    # Some numeric fields may be strings; cast safely (examples)
    if "restaurant_avg_cost_for_2" in df_orders.columns:
        df_orders["restaurant_avg_cost_for_2"] = safe_cast_series_to_int(df_orders["restaurant_avg_cost_for_2"], default=None)
    if "restaurant_rating" in df_orders.columns:
        df_orders["restaurant_rating"] = safe_cast_series_to_float(df_orders["restaurant_rating"], default=None)

    # Ensure order_date exists and parseable (keep as ISO string)
    if "order_date" in df_orders.columns:
        # Try to coerce to datetime then back to ISO string
        try:
            df_orders["order_date_parsed"] = pd.to_datetime(df_orders["order_date"], errors="coerce")
            # fill missing parse by leaving original string
            df_orders["order_date_iso"] = df_orders["order_date_parsed"].dt.strftime("%Y-%m-%d %H:%M:%S")
            df_orders.loc[df_orders["order_date_iso"].isna(), "order_date_iso"] = df_orders.loc[df_orders["order_date_iso"].isna(), "order_date"]
            df_orders["order_timestamp"] = df_orders["order_date_iso"]
        except Exception:
            df_orders["order_timestamp"] = df_orders["order_date"].astype(str)
    else:
        df_orders["order_timestamp"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    # If duplicates of order_id exist, keep the latest by order_timestamp
    print("Deduplicating orders by order_id (keeping latest if duplicates exist)...")
    if "order_timestamp" in df_orders.columns:
        df_orders["__order_ts_sort"] = pd.to_datetime(df_orders["order_timestamp"], errors="coerce")
        # Rows with NaT will be considered oldest; we fill with epoch for deterministic behavior
        df_orders["__order_ts_sort"] = df_orders["__order_ts_sort"].fillna(pd.Timestamp("1970-01-01"))
        df_orders = df_orders.sort_values("__order_ts_sort", ascending=False)
        df_orders = df_orders.drop_duplicates(subset=["order_id"], keep="first").reset_index(drop=True)
        df_orders = df_orders.drop(columns="__order_ts_sort")
    else:
        df_orders = df_orders.drop_duplicates(subset=["order_id"], keep="first").reset_index(drop=True)
    print(f"  Orders after dedup: {len(df_orders)}")

    # 4) Build restaurants DataFrame (deduped)
    print("Building restaurants table from orders data...")
    rest_cols_present = [c for c in ["restaurant_name","restaurant_address","restaurant_city","primary_cuisine","restaurant_avg_cost_for_2","restaurant_rating","restaurant_type"] if c in df_orders.columns]
    df_rest = df_orders[rest_cols_present].drop_duplicates().reset_index(drop=True)
    # rename columns to final table names
    rename_map = {}
    if "restaurant_name" in df_rest.columns: rename_map["restaurant_name"]="name"
    if "restaurant_address" in df_rest.columns: rename_map["restaurant_address"]="address"
    if "restaurant_city" in df_rest.columns: rename_map["restaurant_city"]="city"
    if "primary_cuisine" in df_rest.columns: rename_map["primary_cuisine"]="primary_cuisine"
    if "restaurant_avg_cost_for_2" in df_rest.columns: rename_map["restaurant_avg_cost_for_2"]="avg_cost_for_2"
    if "restaurant_rating" in df_rest.columns: rename_map["restaurant_rating"]="rating"
    if "restaurant_type" in df_rest.columns: rename_map["restaurant_type"]="type"
    df_rest = df_rest.rename(columns=rename_map)
    print(f"  Unique restaurants found: {len(df_rest)}")

    # 5) Create DB and tables, then insert data
    print("Creating SQLite DB and tables...")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Use robust schema with expected columns
    cur.executescript("""
    PRAGMA foreign_keys = OFF;

    CREATE TABLE IF NOT EXISTS restaurants (
        restaurant_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        address TEXT,
        city TEXT,
        type TEXT,
        primary_cuisine TEXT,
        avg_cost_for_2 INTEGER,
        rating REAL
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        customer_id TEXT,
        restaurant_id INTEGER,
        order_timestamp TEXT,
        order_day TEXT,
        order_cost REAL,
        order_user_rating REAL,
        order_delivery_time_mins INTEGER,
        order_time_slot TEXT,
        order_status TEXT,
        ordered_items_count INTEGER,
        order_discount_amount REAL,
        order_amount_paid REAL,
        order_complaint_flag BOOLEAN,
        FOREIGN KEY (restaurant_id) REFERENCES restaurants(restaurant_id)
    );

    CREATE TABLE IF NOT EXISTS order_items (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT,
        food_item TEXT,
        food_item_cost REAL,
        FOREIGN KEY (order_id) REFERENCES orders(order_id)
    );

    PRAGMA foreign_keys = ON;
    """)
    conn.commit()

    # Insert restaurants
    print("Inserting restaurants...")
    # ensure columns exist in df_rest for insertion order
    rest_insert_cols = ["name","address","city","type","primary_cuisine","avg_cost_for_2","rating"]
    for c in rest_insert_cols:
        if c not in df_rest.columns:
            df_rest[c] = None
    # Use pandas to_sql to insert
    df_rest[rest_insert_cols].to_sql("restaurants", conn, if_exists="append", index=False)
    print("  Restaurants inserted.")

    # Build restaurant lookup mapping (lowercased composite key)
    restaurant_map = pd.read_sql_query("SELECT restaurant_id, name, address, city FROM restaurants", conn)
    restaurant_map["key"] = (restaurant_map["name"].astype(str).str.lower().fillna("") + "||" +
                             restaurant_map["address"].astype(str).str.lower().fillna("") + "||" +
                             restaurant_map["city"].astype(str).str.lower().fillna(""))
    rest_lookup = dict(zip(restaurant_map["key"], restaurant_map["restaurant_id"]))

    # Attach restaurant_id to orders (map via key)
    print("Mapping orders to restaurant_id...")
    df_orders["__rest_key"] = make_restaurant_key(df_orders, "restaurant_name", "restaurant_address", "restaurant_city")
    df_orders["restaurant_id"] = df_orders["__rest_key"].map(rest_lookup)

    missing_rest_ids = df_orders["restaurant_id"].isna().sum()
    if missing_rest_ids:
        print(f"WARNING: {missing_rest_ids} orders could not be mapped to restaurants (restaurant_id is NULL).")
        # Option: we can try looser matching (only by name) or assign NULLs. We'll keep NULLs for now.

    # Prepare orders for insertion: keep only columns present in our schema
    orders_columns_map = {
        "order_id": "order_id",
        "customer_id": "customer_id",
        "restaurant_id": "restaurant_id",
        "order_timestamp": "order_timestamp",
        "order_day": "order_day",
        "order_cost": "order_cost",
        "order_user_rating": "order_user_rating",
        "order_delivery_time_mins": "order_delivery_time_mins",
        "order_time_slot": "order_time_slot",
        "order_status": "order_status",
        "ordered_items_count": "ordered_items_count",
        "order_discount_amount": "order_discount_amount",
        "order_amount_paid": "order_amount_paid",
        "order_complaint_flag": "order_complaint_flag"
    }

    # Ensure those columns exist in df_orders; attempt to copy or fill missing
    df_orders_insert = pd.DataFrame()
    for src_col, dst_col in orders_columns_map.items():
        if src_col in df_orders.columns:
            df_orders_insert[dst_col] = df_orders[src_col]
        else:
            # fill missing expected columns with None
            df_orders_insert[dst_col] = None

    # But we used order_timestamp earlier; ensure that's used:
    if "order_timestamp" not in df_orders_insert.columns and "order_date" in df_orders.columns:
        df_orders_insert["order_timestamp"] = df_orders["order_date"]

    # Drop rows with null order_id (shouldn't happen but safe)
    df_orders_insert["order_id"] = df_orders_insert["order_id"].astype(str).str.strip()
    df_orders_insert = df_orders_insert[df_orders_insert["order_id"] != ""].reset_index(drop=True)
    print(f"Orders prepared for insert: {len(df_orders_insert)} rows")

    # Insert orders in one transaction
    print("Inserting orders into DB...")
    df_orders_insert.to_sql("orders", conn, if_exists="append", index=False)
    print("  Orders inserted.")

    # 6) Validate and insert order_items
    print("Validating order_items foreign keys...")
    df_items = df_items.copy()
    # Ensure item columns exist and are named to match DB
    # Heuristic mapping: if df_items has 'food_item' or 'item_name' etc.
    if "food_item" not in df_items.columns and "item_name" in df_items.columns:
        df_items = df_items.rename(columns={"item_name":"food_item"})
    if "food_item_cost" not in df_items.columns and "item_price" in df_items.columns:
        df_items = df_items.rename(columns={"item_price":"food_item_cost"})

    # Keep only required columns
    for col in ["order_id","food_item","food_item_cost"]:
        if col not in df_items.columns:
            df_items[col] = None

    # Trim order_id in items as well
    df_items["order_id"] = df_items["order_id"].astype(str).str.strip()

    valid_order_ids = set(df_orders_insert["order_id"].unique())
    before_items = len(df_items)
    df_items_valid = df_items[df_items["order_id"].isin(valid_order_ids)].reset_index(drop=True)
    removed = before_items - len(df_items_valid)
    print(f"  Filtered order_items: removed {removed} rows with invalid order_id")

    # Save cleaned items CSV if requested
    if cleaned_items_out:
        df_items_valid.to_csv(cleaned_items_out, index=False)
        print(f"  Cleaned order_items saved to: {cleaned_items_out}")

    # Insert order_items (batched if large)
    print("Inserting order_items into DB...")
    df_items_valid[["order_id","food_item","food_item_cost"]].to_sql("order_items", conn, if_exists="append", index=False)
    print(f"  Order items inserted: {len(df_items_valid)}")

    # 7) Create indexes for performance
    print("Creating indexes...")
    cur.executescript("""
        CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
        CREATE INDEX IF NOT EXISTS idx_orders_restaurant_id ON orders(restaurant_id);
        CREATE INDEX IF NOT EXISTS idx_orders_timestamp ON orders(order_timestamp);

        CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
    """)
    conn.commit()

    # Finalize
    conn.close()
    elapsed = datetime.now() - start_ts
    print(f"\nDatabase build completed in {elapsed}. Final counts:")
    print(f"  restaurants: {len(df_rest)}")
    print(f"  orders: {len(df_orders_insert)}")
    print(f"  order_items: {len(df_items_valid)}")
    print("\nDone.\n")

# --- CLI ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build normalized SQLite orders DB from CSVs.")
    parser.add_argument("--db", default=DEFAULT_DB, help="Output SQLite DB path")
    parser.add_argument("--orders", default=DEFAULT_ORDERS_CSV, help="Orders CSV path")
    parser.add_argument("--items", default=DEFAULT_ITEMS_CSV, help="Order items CSV path")
    parser.add_argument("--cleaned-items-out", default="data/data_files/df_order_items_table_cleaned.csv",
                        help="Path to save cleaned order_items CSV (filtered invalid FK rows)")
    args = parser.parse_args()

    build_database(db_path=args.db,
                   orders_csv=args.orders,
                   items_csv=args.items,
                   cleaned_items_out=args.cleaned_items_out,
                   backup_old_db=True)

if __name__ == "__main__":
    main()
