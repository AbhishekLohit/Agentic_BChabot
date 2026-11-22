# Merchant Payments - Payment Holds

This is a common issue. A 'processing' or 'pending' status means the customer's bank has authorised the payment but the settlement is not complete. The bank has placed a temporary authorization hold on the customer's balance. The transaction could clear, or it could fail. As a merchant, you should not release the inventory or service until you receive a successful settlement confirmation or a 'paid' notification from your payment gateway. If you release the inventory and the transaction later fails, you will have to chase the customer for payment or cancel the order .

Set a policy in your system: do not release inventory until you receive a final 'success' status from the gateway. If the transaction is pending for more than 1 hour, contact the customer to inform them that the transaction is still processing. If it fails after a long time, the system will reverse the funds to the customer. Then, you can ask them to pay again. To avoid this, you can configure your gateway to auto-cancel orders that are pending for more than a specific time (e.g., 15 minutes) .

The merchant learns the best practice of not releasing inventory on pending UPI payments.
