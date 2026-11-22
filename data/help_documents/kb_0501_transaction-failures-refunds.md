# Transaction Failures & Refunds

UPI itself operates 24x7x365 and individual P2P (person-to-person) transfers settle instantly even on bank holidays and weekends via NPCI's real-time settlement. However, P2M (person-to-merchant) settlements and NEFT-based components underlying some bank transfers may follow banking day schedules. If your bank quoted a 2-day delay, it is likely: (1) the receiving account is an escrow or nodal account that settles on banking days, (2) a NEFT leg is involved, or (3) the bank erroneously applied NEFT settlement logic. For standard UPI P2P, there should be no holiday delay. As per NPCI's 24x7 UPI architecture.

Check your UPI transaction status — if it shows 'Success', the recipient's bank received the money even if they can't access it until the holiday. If it shows 'Pending', call your bank and specifically ask why a UPI P2P transaction has a holiday delay. If unsatisfied, file a grievance citing NPCI's 24x7 processing mandate.

User learns the difference between UPI P2P (24x7 instant) and P2M settlement cycles (may follow banking days), and knows to check transaction status to distinguish between actual and processing delays.
