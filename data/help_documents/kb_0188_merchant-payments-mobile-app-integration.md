# Merchant Payments - Mobile App Integration

To integrate UPI into your mobile app, you can use a payment gateway like Razorpay, PayU, or Cashfree. These gateways provide SDKs (Software Development Kits) and APIs that make integration easy. The best approach is to use the 'intent' flow, which redirects the customer to their UPI app for authorization. This is the most seamless and secure method. You can also use dynamic QR codes or payment links within the app. Ensure you handle the callback URL correctly to update the order status after payment.

Choose a payment gateway that offers a mobile SDK for UPI. Integrate the SDK into your app. Test the UPI flow thoroughly. Ensure your app handles callbacks and updates the order status in real-time. For a better user experience, display the available UPI apps and allow the customer to choose their preferred one. Also, consider fallback options like NEFT/IMPS for customers who face issues with UPI.

The merchant learns how to integrate UPI into a mobile app using a payment gateway SDK.
