import pandas as pd

# Example dataframe
df = pd.read_excel("df_transaction_data.xlsx")

# Ensure transaction_date is datetime
df["transaction_date"] = pd.to_datetime(df["transaction_date"])

# Find shift so latest date becomes today
today = pd.Timestamp.today().normalize()
max_date = df["transaction_date"].max()
delta = today - max_date

# Shift transaction_date
df["transaction_date"] = df["transaction_date"] + delta

# Update derived columns
df["year"] = df["transaction_date"].dt.year
df["month"] = df["transaction_date"].dt.month
df["day"] = df["transaction_date"].dt.day

df.to_excel("df_transaction_data_preprocessed.xlsx", index=False)