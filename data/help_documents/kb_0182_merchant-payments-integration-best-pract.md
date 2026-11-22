# Merchant Payments - Integration Best Practices

The best practice for e-commerce UPI checkout is to use a combination of 'intent' and dynamic QR code flow. The 'intent' flow redirects the customer to their UPI app and often has the highest success rate. Dynamic QR codes (which include the exact amount and order ID) are also effective, especially for customers who scan via their phone. Keep a 'collect' request as a fallback option. Ensure your UPI integration is NPCI-compliant with brand-compliant QR displays and standardised UPI ID presentation. Non-compliance can result in penalties and customer frustration. Also, integrate webhooks to monitor real-time transaction status and automatically retry or escalate failures .

Work with your payment gateway to implement an 'intent + dynamic QR' flow. Test this flow thoroughly. Keep a 'collect' option as a backup. Ensure the checkout page displays the available UPI apps (Google Pay, PhonePe, Paytm). Add auto-retry logic for intermittent failures. Monitor your success rates by PSP and optimize accordingly. Also, display realistic refund timelines at checkout to manage expectations .

The e-commerce merchant learns the best integration practices for UPI checkout to reduce failures.
