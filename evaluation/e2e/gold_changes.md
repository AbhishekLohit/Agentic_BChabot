# Gold changes

Every change to `golden_cases.yaml` after its first version goes here, with the reason. A
case where the model disagrees with the gold is fixed **only** when the raw data
(transactions.db, the help documents or the dispute config) shows the gold was wrong.

| Date | Case | Change | Evidence it was wrong |
|---|---|---|---|
| 2026-11-25 | all | First version: 80 cases, built answer-first; `validate.py` passes | n/a |
| 2026-11-26 | SQL-11 | Question "how many payments did I make to the second one?" reworded to "how many times did money go to the second one?" (reference unchanged) | The app's own SQL prompt (`prompts/text2sql_generator.yml:91`) defines "payment(s)" as `transaction_type='payment'`, but the merchant (Monthly Savings Transfer) is a standing order, so the old wording contradicted the product's definition |
| 2026-11-26 | HLP-01-12, HLP-16, BND-03, GRD-09, GRD-10, MIX-04 | `key_facts` split into required facts (the ones that answer the question asked) and `optional_facts` (useful extras, shown to the judge as context, never required) | The first version listed extras as required, e.g. "where are my reward points" also required "shows redemption options", which was not asked. Every required fact is still taken from the evidence document |
| 2026-11-26 | HLP-12 | `must_mention: mini statement` replaced by "net banking / mobile banking / app" | Same reason: the question asks whether a FULL statement can be sent by SMS. "No, use net/mobile banking" answers it; the mini statement is now an optional fact |
