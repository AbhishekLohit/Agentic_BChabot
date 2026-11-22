# KYC & Compliance

The error 'PAN not linked' in UPI can stem from: (1) PAN linked at bank level but not propagated to NPCI's mapper, (2) incorrect PAN entry by the bank (character transposition), or (3) UPI apps pulling stale KYC data cached during app registration. The NPCI UPI ecosystem validates PAN via the bank's core banking system and its own central mapper. A mismatch at any level throws this error. As per NPCI's technical error code documentation for UPI apps.

Visit your bank branch with PAN card and request a PAN verification printout from their system. If the PAN in their system is correct, ask them to push an update to NPCI's mapper. Also try de-registering and re-registering your UPI app after 24 hours to force a fresh KYC data fetch.

User understands that UPI KYC validation spans multiple systems (bank, NPCI mapper) and a fix at one level may take time to propagate to others.
