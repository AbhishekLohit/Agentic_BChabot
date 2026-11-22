# Merchant Payments - Chargeback Limits

Yes, NPCI has put a cap on the number of UPI chargebacks a customer can file. As per the rules effective December 2024, a customer can file a maximum of 10 chargebacks per customer in a 30-day rolling period. Additionally, they can file a maximum of 5 chargebacks per payer and payee combination in a 30-day rolling period. Once these limits are crossed, the system will decline the chargeback request with reason codes CD1 (11th chargeback) and CD2 (6th chargeback). This was introduced to prevent abuse of the chargeback system. However, if a legitimate chargeback is declined because of this cap, the new RGNB rules allow the bank to reinitiate it without NPCI permission, provided the claim is genuine .

If you receive a chargeback from a customer who has already filed multiple chargebacks, check the reason code. If it is CD1 or CD2, the system will automatically reject it. However, if the customer's bank believes it's a genuine claim, they can reinitiate it via RGNB. As a merchant, you will still receive a notification and can contest it as usual. Knowing these limits can help you manage chargeback risks more effectively .

The merchant learns about the NPCI-imposed limits on the number of chargebacks a customer can file.
