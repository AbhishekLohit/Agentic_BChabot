#!/usr/bin/env python3

import os
import argparse
import sqlite3
import pandas as pd

def export_table_to_excel(conn, table_name, output_path):
    """
    Read a table from SQLite and save it to Excel.
    """
    print(f"📤 Exporting table: {table_name} ...")

    try:
        df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        df.to_excel(output_path, index=False)
        print(f"   ➤ Saved: {output_path} (Rows: {len(df)})")

    except Exception as e:
        print(f"   ❌ Error exporting {table_name}: {e}")


def export_all_tables(db_path, output_dir):
    """
    Connects to SQLite, finds all tables, exports each into its own Excel file.
    """
    print(f"\n📌 Connecting to database: {db_path}")

    if not os.path.exists(db_path):
        print(f"❌ ERROR: Database file '{db_path}' does not exist.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Fetch table names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]

    print(f"📋 Found tables: {tables}\n")

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Export each table
    for table in tables:
        out_path = os.path.join(output_dir, f"{table}.xlsx")
        export_table_to_excel(conn, table, out_path)

    conn.close()
    print("\n✅ Export complete!\n")


def main():
    parser = argparse.ArgumentParser(description="Export SQLite tables to Excel files.")
    parser.add_argument("--db", default="orders_mar26.db", help="Path to SQLite database file")
    parser.add_argument("--out-dir", default="exported_excels", help="Output directory for Excel files")

    args = parser.parse_args()

    export_all_tables(db_path=args.db, output_dir=args.out_dir)


if __name__ == "__main__":
    main()
