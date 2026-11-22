# Merchant Payments - Status Mismatch

This status mismatch occurs when the customer's bank has approved the payment and debited their account, but the settlement has not yet been confirmed to your payment gateway. This is often a temporary technical issue. The status typically resolves itself within 15-30 minutes. If it persists, it could be due to a delay in the inter-bank settlement. Under RBI rules, if the money is debited from the customer but not credited to you within 24 hours, it will be auto-reversed to the customer.

Wait for 30 minutes and refresh your dashboard. If the status remains 'Pending', check your bank account balance to see if the money has been credited. If you have received the money, update the order status manually. If not, inform the customer that the transaction is still processing and ask them to check their bank statement after 2 hours. If it is still pending after 24 hours, the auto-reversal will happen.

The merchant learns the cause of status mismatch and the recommended waiting period.
