# Merchant Payments - Refund Timelines

Refund timelines vary based on the reason for the refund. Automatic reversals for failed transactions (where you never received the credit) take 1-3 working days. If the transaction was successful but you are initiating a refund (e.g., order cancelled), this is a merchant-initiated manual refund and typically takes 3-7 working days. Technical issues or bank processing cycles can extend this to up to 10 working days. Unlike card payments, UPI does not support partial or instant refunds in all cases .

For faster refunds, ensure your payment gateway processes refunds immediately. Use webhooks to trigger instant customer notifications when you initiate a refund. Reconcile your settlements daily and auto-escalate any refund that goes beyond the standard TAT. Display realistic, channel-specific refund SLAs at checkout and in communication. This manages customer expectations and reduces support tickets .

The user learns why UPI refunds take different times and how to manage customer expectations.
