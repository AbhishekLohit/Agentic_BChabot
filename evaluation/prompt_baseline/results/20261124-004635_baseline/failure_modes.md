# How Qwen/Qwen3-8B breaks (single-agent baseline)

Measured on 42 questions (2026-11-24T00:46:35). Counts are questions, per strategy. Edit the wording; keep the numbers and examples from report.md.

Best single-prompt strategy: **structured_fewshot** (38/42 correct).

## wrong_result

- **What happens:** The SQL ran but gave a different answer (wrong filter, aggregation, date range or definition of 'spending').
- **How often:** zero_shot 8, structured_fewshot 3, cot 8, self_consistency 5, decomposition 7
- **Example:** see report.md › Examples
- **How the multi-agent app handles it:** Few-shot SQL examples and explicit definitions; evaluation on a frozen set.

## empty_result

- **What happens:** The SQL ran but returned nothing / NULL where an answer exists (usually a wrong filter value or date).
- **How often:** zero_shot 1, structured_fewshot 0, cot 2, self_consistency 2, decomposition 2
- **Example:** see report.md › Examples
- **How the multi-agent app handles it:** Entity extraction of real merchant/category values; dates resolved in code.

## wrong_route

- **What happens:** The question was sent to the wrong place (e.g. a how-to treated as a dispute).
- **How often:** zero_shot 0, structured_fewshot 1, cot 0, self_consistency 0, decomposition 0
- **Example:** see report.md › Examples
- **How the multi-agent app handles it:** A dedicated supervisor prompt with contrastive examples per pair of routes.
