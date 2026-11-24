# How Qwen/Qwen3-8B breaks (single-agent baseline)

Measured on 5 questions (2026-11-24T00:43:22). Counts are questions, per strategy. Edit the wording; keep the numbers and examples from report.md.

Best single-prompt strategy: **structured_fewshot** (5/5 correct).

## wrong_result

- **What happens:** The SQL ran but gave a different answer (wrong filter, aggregation, date range or definition of 'spending').
- **How often:** zero_shot 1, structured_fewshot 0, cot 2, self_consistency 2, decomposition 2
- **Example:** see report.md › Examples
- **How the multi-agent app handles it:** Few-shot SQL examples and explicit definitions; evaluation on a frozen set.
