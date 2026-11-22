# Merchant Payments - Duplicate Deductions

If a customer's UPI payment was deducted twice, you will typically see two separate credits in your settlement. You need to initiate a refund for one of the duplicate transactions. This is a merchant-initiated refund. You should process the refund through your payment gateway or bank using the UTR of the transaction you want to refund. The refund will be credited back to the customer's account. The process is the same as a standard refund; you just need to ensure you are refunding the correct transaction.

Identify the duplicate transaction in your settlement report. Note the UTR. Initiate a refund through your payment gateway or bank for that specific transaction. Inform the customer that the refund is being processed and provide the refund reference number. The refund timeline is 3-7 working days. If you are using a gateway, you can also ask them to auto-detect and reverse duplicates to avoid manual intervention.

The merchant learns how to handle duplicate UPI deductions and process refunds.
