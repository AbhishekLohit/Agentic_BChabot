# Transaction Failures & Refunds

Multiple retries during a timeout are a common cause of duplicate deductions. NPCI's reconciliation process should automatically reverse the duplicate transactions within T+1. However, in cases where all 5 transactions received UTR numbers (all processed), manual intervention is needed. You should not retry a UPI transaction multiple times — if a transaction shows 'Processing', wait for 30 minutes before concluding it failed, as retrying during processing is the root cause of duplicates. As per NPCI's retry policy for UPI transactions.

Check all 5 transaction UTR numbers in your bank statement. File a complaint with your bank citing all duplicate UTRs, specifying which one was the intended transaction. The bank will verify with NPCI and reverse the duplicates. Keep the original transaction and request reversal of the other 4.

User learns to avoid retrying UPI transactions during 'Processing' state, and knows the dispute process for recovering multiple duplicate deductions.
