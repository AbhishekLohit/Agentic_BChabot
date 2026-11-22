# Merchant Payments - Failed Transaction Recovery

If a customer's UPI payment is debited but not credited to you (the merchant), the RBI mandates a strict timeline for auto-reversal. For merchant payments (P2M), the customer's bank must process an auto-reversal of the debited amount within T+5 days, where T is the transaction day. If the delay extends beyond these 5 days, the customer's bank is liable to pay a compensation of ₹100 per day of delay to the customer. The funds are credited back to the customer's account, not to your merchant account, so you will need to ask them to pay again. This rule applies specifically to merchant transactions and was established by the RBI in its harmonisation of Turn Around Time (TAT) guidelines .

First, check your merchant settlement report to confirm the credit is missing. Then, inform the customer about the auto-reversal process, which will return the funds to their account. Ask them to check their bank statement and initiate a fresh payment. If the issue persists beyond the RBI timeline, you can guide them to file a complaint with their bank. If you are a merchant using a payment aggregator or gateway, also check their dashboard for any pending settlements.

The user learns the specific RBI rule for failed P2M UPI transactions, including the compensation timeline and that refunds go to the customer.
