import csv, json, re, sys
from collections import Counter
from pathlib import Path
DOCS = Path(sys.argv[1]); OUT = Path(sys.argv[2])
files = {f.name[:7]: f.name for f in DOCS.glob("kb_*.md")}
text = {k: re.sub(r"\s+", " ", (DOCS / v).read_text(encoding="utf-8")).lower() for k, v in files.items()}

def ev(doc, quote): return {"doc": files[doc], "section": None, "page": None, "quote": quote}
def g(*alts): return [ev(d, q) for d, q in alts]

ITEMS = [
("E001", "paraphrase", "I'm trying to set my UPI PIN but the app keeps saying it can't find my debit card. What's going wrong?",
 [g(("kb_0623", "The error 'Debit card not found' usually means that your bank account is not linked to any active debit card"))],
 "Your bank account is probably not linked to an active debit card, or the card is expired, blocked or not enabled for online use. Virtual debit cards may not work; try the physical card, re-enter the details carefully, or ask your bank to enable the card for UPI registration.",
 ["account not linked to an active debit card", "card expired, blocked or not activated for online use", "virtual cards may not work; try the physical card", "contact the bank if it persists"]),
("E002", "factual", "Is there a maximum amount I can send by NEFT? I need to transfer 5 lakh rupees.",
 [g(("kb_0438", "There is no RBI-set upper limit for NEFT, though banks may have their own internal limits"),
    ("kb_0584", "NEFT and RTGS have no upper limit for online transactions"),
    ("kb_0583", "NEFT (which has no upper limit but is processed in batches)"))],
 "There is no RBI-set upper limit for NEFT, though your bank may have its own limit. For large amounts RTGS is faster because it settles in real time, while NEFT works in batches.",
 ["no RBI-set upper limit for NEFT", "banks may set their own limits", "RTGS is faster for large amounts"]),
("E003", "reasoning", "I plan to send my sister abroad a gift of 15 lakh rupees this financial year. How much tax will be collected at source?",
 [g(("kb_0085", "TCS at 5% on the amount exceeding ₹10 lakh in a financial year"),
    ("kb_0087", "TCS at 5% above ₹10 lakh under LRS applies"),
    ("kb_0080", "otherwise, it is 5% above ₹10 lakh"))],
 "TCS is 5% on the amount above ₹10 lakh in a financial year, so on ₹15 lakh it is 5% of ₹5 lakh = ₹25,000. You can claim it as a credit in your income tax return.",
 ["5% on the amount above ₹10 lakh", "₹25,000 on ₹15 lakh", "can be claimed as credit in the tax return"]),
("E004", "paraphrase", "Someone who looked like a senior government officer video-called me on WhatsApp and asked me to pay into an emergency fund by UPI. Should I pay?",
 [g(("kb_0677", "No government agency will ever request UPI payments for any 'national emergency fund' or purpose via WhatsApp"))],
 "No. This is an AI deepfake video-call scam; no government agency asks for UPI payments over WhatsApp. End the call, don't pay, and report the number at cybercrime.gov.in.",
 ["do not pay", "deepfake impersonation scam", "government never requests UPI payments via WhatsApp", "report at cybercrime.gov.in"]),
("E005", "factual", "How often does the bank make me update my KYC?",
 [g(("kb_0120", "low-risk customers every 10 years, medium-risk every 8 years, and high-risk every 2 years"),
    ("kb_0138", "high-risk customers must update every 2 years, medium-risk every 8 years, and low-risk every 10 years"))],
 "It depends on your risk category: every 10 years for low-risk, 8 years for medium-risk and 2 years for high-risk customers. If you don't update when asked, the bank must restrict and then freeze debits on the account.",
 ["low-risk every 10 years", "medium-risk every 8 years", "high-risk every 2 years", "account restricted then frozen if not updated"]),
("E006", "keyword", "What is a KRA when opening a demat account?",
 [g(("kb_0129", "A KRA (KYC Registration Agency) is a SEBI-registered entity that maintains a centralized KYC database for the securities market"))],
 "A KYC Registration Agency: a SEBI-registered entity holding a central KYC database for the securities market. Once your KYC is with a KRA, another broker can fetch it, so you don't resubmit documents.",
 ["KYC Registration Agency", "SEBI-registered central KYC database", "other brokers can fetch it without resubmission"]),
("E007", "near_miss", "A stranger sent money to my UPI account. Can I reject the payment?",
 [g(("kb_0492", "You cannot 'refuse' an incoming push payment"))],
 "No, an incoming UPI push payment can't be refused. Don't spend it; ask your bank to return it to the sender, and never send it to a different UPI ID the sender gives you, which is an overpayment scam.",
 ["cannot refuse an incoming UPI payment", "do not spend the money", "return it through the bank", "do not send it to an alternate UPI ID"]),
("E008", "reasoning", "I noticed a ₹1 debit on my account that I don't recognise. Should I worry about something so small?",
 [g(("kb_0023", "Fraudsters often test compromised accounts with a small ₹1 transaction to confirm the account is active"))],
 "Yes. A ₹1 debit can be fraudsters testing whether your account works before a larger fraud, though it may also be a subscription verification charge. Check the merchant name, call your bank if it's unknown, and change your UPI PIN.",
 ["may be a fraud test before a larger fraud", "could be a subscription verification charge", "check the merchant and call the bank", "change UPI PIN"]),
("E009", "procedural", "My account was frozen after a police complaint about a mule transaction I had nothing to do with. What should I do?",
 [g(("kb_0374", "File an application at the Bank's nodal officer for access to a minimum livelihood amount even during freeze"),
    ("kb_0364", "right to access minimum livelihood funds even during freeze"))],
 "Hire a criminal advocate, submit a counter-affidavit to the investigating police station with your transaction history, apply to the bank's nodal officer for a minimum livelihood amount during the freeze, and apply to the court to unfreeze the account.",
 ["hire a criminal advocate", "counter-affidavit with transaction history", "minimum livelihood amount during the freeze", "apply to the court to unfreeze"]),
("E010", "procedural", "How do I add a nominee to my savings account at the branch?",
 [g(("kb_0039", "You need to fill out a 'Nomination Form' (Form DA-1 for most banks) at your bank branch"),
    ("kb_0481", "Form DA-1 for savings accounts"))],
 "Fill in the Nomination Form (Form DA-1 at most banks) at your branch with your PAN and Aadhaar for verification and the nominee's name, address and date of birth. It takes about 5-10 minutes; some banks also allow it online.",
 ["Form DA-1", "PAN and Aadhaar for verification", "nominee's name, address and date of birth", "about 5-10 minutes"]),
("E011", "procedural", "I locked my Aadhaar biometrics and now Aadhaar-based cash withdrawals fail. How can I use them again?",
 [g(("kb_0002", "UIDAI introduced a 'Temporary Unlock' feature in the mAadhaar app that automatically re-locks biometrics after a set period"))],
 "A biometric lock blocks AEPS. Use 'Temporary Unlock' in the mAadhaar app (about 10 minutes, then it re-locks), and do the transaction in that window. Without a smartphone, unlock via UIDAI's IVR at 1947 (needs an OTP) or at an Aadhaar centre.",
 ["biometric lock blocks AEPS", "temporary unlock in the mAadhaar app", "re-locks automatically after about 10 minutes", "IVR 1947 alternative"]),
("E012", "paraphrase", "My bank took ₹177 as 'incidental charges' without saying what for. Are they allowed to do that?",
 [g(("kb_0042", "Not levy charges that are not in their published tariff"))],
 "Banks must publish their charges, explain any charge on request and cannot levy charges not in their tariff. Complain in writing to the bank's Grievance Redressal Officer, check the schedule of charges, and escalate to RBI CMS if unresolved after 30 days.",
 ["banks must explain charges", "cannot levy charges not in the published tariff", "complain to the Grievance Redressal Officer", "escalate to RBI CMS after 30 days"]),
("E013", "factual", "How much overdraft can I get on a Jan Dhan account?",
 [g(("kb_0100", "The PMJDY overdraft facility of up to ₹10,000 (with the first ₹2,000 given without conditions for accounts held by women)"))],
 "Up to ₹10,000, with the first ₹2,000 without conditions for accounts held by women. The account must be operated satisfactorily for 6 months, be Aadhaar-seeded, and only one account per household is eligible.",
 ["up to ₹10,000", "₹2,000 without conditions for women", "6 months of satisfactory operation", "Aadhaar-seeded; one account per household"]),
("E014", "near_miss", "Do I have to pay back my whole Kisan Credit Card balance before the yearly renewal?",
 [g(("kb_0145", "You do NOT have to repay the full amount to renew"))],
 "No. The KCC is a revolving facility valid for 5 years with an annual review, not a fresh application, so you don't repay the full amount to renew.",
 ["no full repayment needed", "annual review, not a fresh application", "valid for 5 years"]),
("E015", "factual", "Is there a limit on how much of my income can go to microfinance loan repayments?",
 [g(("kb_0240", "An NBFC-MFI cannot give a loan that takes your total EMI beyond 50% of your monthly household income"))],
 "Yes. Under RBI's 2022 microfinance rules, an NBFC-MFI cannot lend if your total EMIs would exceed 50% of your monthly household income.",
 ["50% of monthly household income", "RBI microfinance regulations 2022"]),
("E016", "keyword", "Can I pay my municipal property tax through BBPS?",
 [g(("kb_0025", "BBPS covers over 20,000 billers across utilities, municipalities, and government services"))],
 "Often yes. Many municipal corporations are on BBPS (it covers over 20,000 billers), so you can pay through the bill-payment section of a UPI app. Check the BBPS biller list for your city.",
 ["many municipalities are on BBPS", "pay via the bill section of a payment app", "check the BBPS biller list"]),
("E017", "multi_section", "Cash wasn't dispensed both at an ATM and at a village banking agent's micro-ATM, but my account was debited both times. How long does the bank have to refund each?",
 [g(("kb_0007", "your bank must automatically reverse the debit within T+5 working days (T = transaction date)")),
  g(("kb_0028", "the bank must auto-reverse the amount within T+5 working days from the date of complaint"))],
 "In both cases the bank must reverse the amount within T+5 working days. For the ATM it counts from the transaction date, with ₹100 per day compensation for delays; for the agent's AEPS transaction it counts from the date of your complaint.",
 ["ATM: T+5 working days from the transaction date", "₹100 per day compensation for ATM delays", "AEPS/agent: T+5 working days from the complaint"]),
("E018", "multi_section", "There's a ₹1 debit I don't recognise, and a stranger also sent money to my UPI. What should I do about each?",
 [g(("kb_0023", "Fraudsters often test compromised accounts with a small ₹1 transaction to confirm the account is active")),
  g(("kb_0492", "You cannot 'refuse' an incoming push payment"))],
 "Treat the ₹1 debit as a possible fraud test: check the merchant, call your bank and change your UPI PIN. The incoming money can't be refused; don't spend it, and ask your bank to return it to the sender rather than sending it to any UPI ID they give you.",
 ["₹1 debit may be a fraud test", "call the bank and change UPI PIN", "incoming payment cannot be refused", "return via the bank, not to an alternate UPI ID"]),
]
UNANSWERABLE = [
 ("U001", "What is the annual rent for a bank locker?", r"\blocker"),
 ("U002", "How much gold loan can I get per gram of gold?", r"gold loan"),
 ("U003", "What extra FD interest rate do senior citizens get?", r"senior citizen.{0,40}(FD|fixed deposit).{0,40}rate"),
 ("U004", "How do I get my credit card limit increased?", r"credit (card )?limit"),
]

rows, problems = [], []
for iid, qtype, q, groups, ref, facts in ITEMS:
    for grp in groups:
        for e in grp:
            if re.sub(r"\s+", " ", e["quote"]).lower() not in text[e["doc"][:7]]:
                problems.append(f"{iid}: quote not found in {e['doc']}: {e['quote']!r}")
    rows.append({"id": iid, "question": q, "question_type": qtype, "answerable": True,
                 "expected_behavior": "answer_with_citations", "gold_doc": groups[0][0]["doc"],
                 "evidence_groups": groups, "reference_answer": ref, "key_facts": facts, "notes": ""})
for iid, q, pat in UNANSWERABLE:
    found = [k for k, t in text.items() if re.search(pat, t, re.I)]
    if found: problems.append(f"{iid}: corpus is NOT silent on this, found in {found[:5]}")
    rows.append({"id": iid, "question": q, "question_type": "unanswerable", "answerable": False,
                 "expected_behavior": "abstain", "gold_doc": None, "evidence_groups": [],
                 "reference_answer": "The available help documents do not cover this.",
                 "key_facts": ["says the documents do not contain the answer", "does not invent figures or steps"],
                 "notes": f"verified: no document matches /{pat}/"})
by_type = {}
for r in rows: by_type.setdefault(r["question_type"], []).append(r)
for items in by_type.values():
    for i, r in enumerate(sorted(items, key=lambda x: x["id"])):
        r["split"] = "test" if i % 3 == 2 else "dev"
if problems:
    print("\n".join(problems)); sys.exit(1)
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "rag_eval_set.jsonl").open("w", encoding="utf-8", newline="\n") as f:
    for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
with (OUT / "rag_eval_set.csv").open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "split", "question_type", "answerable", "question", "gold_documents", "evidence_quotes", "reference_answer", "key_facts", "notes"])
    for r in rows:
        w.writerow([r["id"], r["split"], r["question_type"], r["answerable"], r["question"],
                    " AND ".join(" | ".join(e["doc"] for e in grp) for grp in r["evidence_groups"]),
                    " AND ".join(" | ".join(e["quote"] for e in grp) for grp in r["evidence_groups"]),
                    r["reference_answer"], "; ".join(r["key_facts"]), r["notes"]])
print(f"OK: {len(rows)} questions, all quotes verified, unanswerables confirmed absent")
print("types:", dict(Counter(r["question_type"] for r in rows)), "| splits:", dict(Counter(r["split"] for r in rows)))
