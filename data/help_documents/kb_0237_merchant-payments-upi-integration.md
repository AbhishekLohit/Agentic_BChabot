# Merchant Payments - UPI Integration

The 'intent' flow redirects the customer to their UPI app (like Google Pay, PhonePe) after they enter their UPI ID on your website. The customer authorizes the payment in their app, and is then redirected back to your website. The 'collect' flow is a push-based method where the merchant initiates a payment request (collect request) to the customer's UPI ID, and the customer receives a notification to approve the payment. The intent flow generally has a higher success rate because the customer is actively authorizing the payment, while the collect flow can fail if the customer doesn't see the notification in time. The intent flow is recommended for e-commerce websites.

If you are building an e-commerce website, integrate the 'intent' flow with your payment gateway. Most gateways like Razorpay and PayU provide this as a standard option. The 'collect' flow can be used as a fallback for customers who prefer it. Ensure your checkout page supports both options for maximum coverage.

The merchant learns the difference between 'intent' and 'collect' UPI flows and which one to use.
