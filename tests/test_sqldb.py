import sqlite3
import pandas as pd

# Path to your SQLite database
DB_PATH = "orders.db"

# Connect to the database
conn = sqlite3.connect(DB_PATH)

# 1️⃣ List all tables
print("📋 Tables in database:")
tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table';", conn)
print(tables, "\n")

# 2️⃣ Show first 5 rows of your item orders table
print("🧾 Sample data from 'order_items' table:")
df = pd.read_sql_query("SELECT * FROM order_items LIMIT 5;", conn)
print(df, "\n")

# 2️⃣ Show first 5 rows of your  orders table
print("🧾 Sample data from 'orders' table:")
df = pd.read_sql_query("SELECT * FROM orders LIMIT 5;", conn)
print(df, "\n")

# 3️⃣ Count how many rows you have in item orders table
count = pd.read_sql_query("SELECT COUNT(*) as total_rows FROM order_items;", conn)
print("📊 Total rows:", count["total_rows"].iloc[0])

# 3️⃣ Count how many rows you have in orders table
count = pd.read_sql_query("SELECT COUNT(*) as total_rows FROM orders;", conn)
print("📊 Total rows:", count["total_rows"].iloc[0])

# Close connection
conn.close()