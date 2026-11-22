# Transaction Failures & Refunds

What you're experiencing is a caching or display refresh issue in your UPI app, not an actual double deduction. When a UPI transaction succeeds, the balance deduction and transaction confirmation are processed nearly simultaneously at the bank's core banking system, but the app's displayed balance may lag behind by a few seconds to minutes depending on the app's refresh cycle and your network speed. The actual bank ledger (accessible via net banking) will show the correct post-transaction balance. As per NPCI's UPI real-time settlement documentation.

Log into your bank's net banking portal or official bank app (not the UPI app) to verify your actual account balance. If the balance in net banking matches the expected post-deduction amount, the transaction was successful and the UPI app display is just stale. Force-refresh the UPI app or log out and log back in.

User learns to distinguish between UPI app display caching issues and actual duplicate deductions, and knows to use net banking as the authoritative balance source.
