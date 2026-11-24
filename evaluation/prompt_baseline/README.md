# Week 1 baseline: single agent, single prompt

Before the multi-agent graph, one LLM call does the whole job: pick the route
(BankSQL / HelpCenter / Dispute / Out_of_scope) and, for data questions, write the SQL.
This folder compares prompting strategies on that task and records **how the small model breaks**.

| Strategy | What it is | Calls per question |
|---|---|---|
| `zero_shot` | Baseline: instructions only, JSON as plain text | 1 |
| `structured_fewshot` | Schema-enforced structured output + 6 worked examples | 1 |
| `cot` | Chain-of-thought: a `reasoning` field before the answer | 1 |
| `self_consistency` | CoT sampled 5× at temperature 0.7, majority vote on the result | 5 |
| `decomposition` | Sub-questions and answers first, then the query | 1 |

## Files
- `cases.jsonl`: 42 questions (24 BankSQL with reference SQL, 8 Help, 4 Dispute, 4 out of scope, 2 safety). Customer `Cust_0410`, "today" fixed at 2026-11-24.
- `prompts/`: shared task text (`_task.md`) and one YAML per strategy.
- `scoring.py`: SQL is run read-only and its **result** compared with the reference query's; each miss gets a failure label.
- `run.py`: runs the comparison, writes the report.

## Run (from the project root, venv active, `.env` as for the app)
```
python evaluation/prompt_baseline/run.py --validate-only     # check cases + prompts, no model calls
python evaluation/prompt_baseline/run.py --limit 5           # quick smoke test (~45 calls)
python evaluation/prompt_baseline/run.py                     # full run (~378 calls with Qwen)
python evaluation/prompt_baseline/run.py --strategies cot decomposition --ids B21 B24
python evaluation/prompt_baseline/run.py --delay 1           # slow down if the shared API rate-limits
python -m pytest tests/test_prompt_baseline.py -q      # offline tests
```
Output goes to `results/<time>_<label>/` (git-ignored): `<strategy>.jsonl` (every raw answer),
`report.md` (comparison tables + examples), `failure_modes.md` (draft write-up), `summary.json`.

## Failure labels
`api_error`, `truncated`, `parse_error`, `thinking_leak` (Qwen `<think>` text in the answer), `invalid_route`,
`wrong_route`, `missing_sql`, `unsafe_sql`, `missing_customer_filter`, `sql_error_no_such_column` / `_no_such_table` /
`_syntax` / `_other`, `empty_result`, `wrong_result`. Each label's meaning and the multi-agent app's mitigation are in `scoring.FAILURES`.

## Writing it up
Paste the comparison table from `report.md`, then edit `failure_modes.md`: keep the counts and examples,
add one sentence per failure on why it happens and which part of the multi-agent design fixes it
(supervisor routing, SQL validator, customer-ID injection, dispute workflow, guardrail).
