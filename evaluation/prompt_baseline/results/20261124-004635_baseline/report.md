# Prompt-engineering baseline: single agent, single prompt

Model: **Qwen/Qwen3-8B** · cases: 42 · today = 2026-11-24 · run 2026-11-24T00:46:35 · self-consistency samples: 5

One LLM call per sample routes the question and, for data questions, writes the SQL. SQL is scored by running it read-only and comparing its RESULT with the reference query's.

## Comparison

| Strategy | Correct overall | Route accuracy | SQL accuracy (data questions) | Valid output | Unsafe SQL | No customer filter | Calls / question | Avg latency (s) | Avg tokens |
|---|---|---|---|---|---|---|---|---|---|
| zero_shot | 79% (33/42) | 100% | 62% (15/24) | 100% | 0 | 0 | 1.0 | 1.62 | 618 |
| structured_fewshot | 90% (38/42) | 98% | 88% (21/24) | 100% | 0 | 0 | 1.0 | 1.56 | 853 |
| cot | 76% (32/42) | 100% | 58% (14/24) | 100% | 0 | 0 | 1.0 | 4.62 | 821 |
| self_consistency | 83% (35/42) | 100% | 71% (17/24) | 100% | 0 | 0 | 5.0 | 21.17 | 4109 |
| decomposition | 79% (33/42) | 100% | 62% (15/24) | 100% | 0 | 0 | 1.0 | 4.09 | 794 |

## Correct by difficulty

| Strategy | Easy | Medium | Hard |
|---|---|---|---|
| zero_shot | 83% (10/12) | 80% (12/15) | 73% (11/15) |
| structured_fewshot | 100% (12/12) | 87% (13/15) | 87% (13/15) |
| cot | 83% (10/12) | 80% (12/15) | 67% (10/15) |
| self_consistency | 92% (11/12) | 80% (12/15) | 80% (12/15) |
| decomposition | 83% (10/12) | 80% (12/15) | 73% (11/15) |

## Route accuracy by expected route

| Strategy | BankSQL | HelpCenter | Dispute | Out_of_scope |
|---|---|---|---|---|
| zero_shot | 100% (24/24) | 100% (8/8) | 100% (4/4) | 100% (6/6) |
| structured_fewshot | 100% (24/24) | 88% (7/8) | 100% (4/4) | 100% (6/6) |
| cot | 100% (24/24) | 100% (8/8) | 100% (4/4) | 100% (6/6) |
| self_consistency | 100% (24/24) | 100% (8/8) | 100% (4/4) | 100% (6/6) |
| decomposition | 100% (24/24) | 100% (8/8) | 100% (4/4) | 100% (6/6) |

## How it breaks: failure modes (count of questions)

| Failure | zero_shot | structured_fewshot | cot | self_consistency | decomposition |
|---|---|---|---|---|---|
| wrong_result | 8 | 3 | 8 | 5 | 7 |
| empty_result | 1 | 0 | 2 | 2 | 2 |
| wrong_route | 0 | 1 | 0 | 0 | 0 |

## Self-consistency: how stable are the answers?

- Average agreement with the majority answer: **0.93**
- Questions where the samples did not all agree: **9** of 42

## Examples of each failure

**wrong_result**: The SQL ran but gave a different answer (wrong filter, aggregation, date range or definition of 'spending').
- `zero_shot` B03 "What was my total income in 2024?" → route BankSQL; `SELECT SUM(ROUND(amount_usd, 2)) AS total_income FROM transactions WHERE customer_id = 'Cust_0410' AND transaction_type = 'income' AND year = 2024`
- `zero_shot` B06 "What is the total of my standing orders in 2026?" → route BankSQL; `SELECT SUM(amount_usd) AS total_standing_orders FROM transactions WHERE customer_id = 'Cust_0410' AND transaction_type = 'standing_order'`

**empty_result**: The SQL ran but returned nothing / NULL where an answer exists (usually a wrong filter value or date).
- `zero_shot` B07 "How much have I paid Netflix in 2026?" → route BankSQL; `SELECT SUM(ROUND(amount_usd, 2)) AS total_paid_netflix FROM transactions WHERE customer_id = 'Cust_0410' AND merchant_brand_name = 'Netflix' AND transaction_type = 'payment' AND year = 2026`
- `cot` B07 "How much have I paid Netflix in 2026?" → route BankSQL; `SELECT SUM(ROUND(amount_usd, 2)) AS total_paid_to_netflix
FROM transactions
WHERE customer_id = 'Cust_0410'
  AND transaction_type = 'payment'
  AND merchant_brand_name = 'Netflix'
  AND year = 2026`

**wrong_route**: The question was sent to the wrong place (e.g. a how-to treated as a dispute).
- `structured_fewshot` H07 "Is it safe to use UPI on public Wi-Fi?" → route Out_of_scope; `{"route": "Out_of_scope", "sql": null}`

