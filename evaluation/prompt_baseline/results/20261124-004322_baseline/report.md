# Prompt-engineering baseline: single agent, single prompt

Model: **Qwen/Qwen3-8B** · cases: 5 · today = 2026-11-24 · run 2026-11-24T00:43:22 · self-consistency samples: 5

One LLM call per sample routes the question and, for data questions, writes the SQL. SQL is scored by running it read-only and comparing its RESULT with the reference query's.

## Comparison

| Strategy | Correct overall | Route accuracy | SQL accuracy (data questions) | Valid output | Unsafe SQL | No customer filter | Calls / question | Avg latency (s) | Avg tokens |
|---|---|---|---|---|---|---|---|---|---|
| zero_shot | 80% (4/5) | 100% | 80% (4/5) | 100% | 0 | 0 | 1.0 | 1.81 | 628 |
| structured_fewshot | 100% (5/5) | 100% | 100% (5/5) | 100% | 0 | 0 | 1.0 | 1.92 | 861 |
| cot | 60% (3/5) | 100% | 60% (3/5) | 100% | 0 | 0 | 1.0 | 5.28 | 846 |
| self_consistency | 60% (3/5) | 100% | 60% (3/5) | 100% | 0 | 0 | 5.0 | 23.27 | 4206 |
| decomposition | 60% (3/5) | 100% | 60% (3/5) | 100% | 0 | 0 | 1.0 | 4.3 | 805 |

## Correct by difficulty

| Strategy | Easy | Medium | Hard |
|---|---|---|---|
| zero_shot | 80% (4/5) | - (0/0) | - (0/0) |
| structured_fewshot | 100% (5/5) | - (0/0) | - (0/0) |
| cot | 60% (3/5) | - (0/0) | - (0/0) |
| self_consistency | 60% (3/5) | - (0/0) | - (0/0) |
| decomposition | 60% (3/5) | - (0/0) | - (0/0) |

## Route accuracy by expected route

| Strategy | BankSQL | HelpCenter | Dispute | Out_of_scope |
|---|---|---|---|---|
| zero_shot | 100% (5/5) | - (0/0) | - (0/0) | - (0/0) |
| structured_fewshot | 100% (5/5) | - (0/0) | - (0/0) | - (0/0) |
| cot | 100% (5/5) | - (0/0) | - (0/0) | - (0/0) |
| self_consistency | 100% (5/5) | - (0/0) | - (0/0) | - (0/0) |
| decomposition | 100% (5/5) | - (0/0) | - (0/0) | - (0/0) |

## How it breaks: failure modes (count of questions)

| Failure | zero_shot | structured_fewshot | cot | self_consistency | decomposition |
|---|---|---|---|---|---|
| wrong_result | 1 | 0 | 2 | 2 | 2 |

## Self-consistency: how stable are the answers?

- Average agreement with the majority answer: **0.96**
- Questions where the samples did not all agree: **1** of 5

## Examples of each failure

**wrong_result**: The SQL ran but gave a different answer (wrong filter, aggregation, date range or definition of 'spending').
- `zero_shot` B03 "What was my total income in 2024?" → route BankSQL; `SELECT SUM(ROUND(amount_usd, 2)) AS total_income FROM transactions WHERE customer_id = 'Cust_0410' AND transaction_type = 'income' AND year = 2024`
- `cot` B01 "How much did I spend in 2025?" → route BankSQL; `SELECT SUM(ROUND(amount_usd, 2)) AS total_spent FROM transactions WHERE customer_id = 'Cust_0410' AND transaction_type <> 'income' AND year = 2025`

