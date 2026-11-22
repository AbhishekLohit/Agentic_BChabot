# Transaction Failures & Refunds

This 'split-brain' scenario occurs when the UPI payment at the NPCI/bank level succeeds but the payment gateway's webhook notification from NPCI fails or times out, causing the gateway to mark the order as failed while the customer's bank debited successfully. The money did leave your account and is held at NPCI level in a reconciliation state — neither credited to the merchant nor returned to you yet. The merchant's gateway must reconcile against the UTR number. As per NPCI's payment gateway integration specifications.

Provide your UTR (Unique Transaction Reference) to the merchant immediately. Ask them to share it with their payment gateway team for manual reconciliation. If the gateway confirms the UTR as successful, the order should be fulfilled. If the gateway cannot reconcile, file a dispute with your bank citing the UTR for credit to your account.

User learns about the webhook failure scenario in UPI payment gateways, and knows that providing the UTR number is the key to resolving success/failure contradictions.
