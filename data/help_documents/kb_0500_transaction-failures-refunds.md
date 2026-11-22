# Transaction Failures & Refunds

UPI AutoPay (recurring mandates) follows a two-step process: debit from your bank account and credit notification to the merchant's payment aggregator. If the notification step fails due to a gateway timeout while the debit step succeeded, your bank shows a debit but the merchant's system shows a failure. This is a known integration issue in the UPI AutoPay ecosystem, especially when platform-side servers experience high load on billing cycles. As per NPCI's UPI AutoPay technical framework.

Share your UPI AutoPay transaction UTR with the streaming platform's support team. Ask them to verify the UTR against their payment aggregator's reconciliation data. If they confirm receipt after reconciliation, your subscription should be reinstated. If they cannot reconcile, file a dispute with your bank for credit reversal.

User learns the two-step UPI AutoPay debit-and-notification architecture and knows to share the UTR number with the platform as the key evidence for subscription reconciliation.
