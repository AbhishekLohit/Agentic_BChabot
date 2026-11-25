# End-to-end evaluation

Runs a golden set of 80 cases through the **real** FinTell agent graph and reports:

- **end-to-end accuracy**: is the final reply right?
- **task completion rate**: did the case reach its goal? For disputes, this is checked in the database.
- **sub-agent selection accuracy**: the gate, the supervisor's routes and the sub-agents that actually ran. The app has no tool calling as such, so this stands in for "tool calls".
- **LLM calls and tool calls per task**: tool calls count SQL runs, help retrievals and dispute lookups.
- **cost and latency per query**: tokens and USD per task (priced by model, see `utils/agent_utils.MODEL_PRICES`), and wall-clock time per turn (mean, p50, p95).
- **a failure taxonomy** with four buckets: retrieval miss, reasoning error, tool-call error and hallucination. Each failure goes to the bucket of its **first failing stage**.

Runs are traced in LangSmith when `LANGCHAIN_API_KEY` is set. Each trace is named `<case>#t<turn>` and tagged with the run label, category and split. SQL queries and dispute lookups show up as `tool` spans.

## Files

| File | What it is |
|---|---|
| `golden_cases.yaml` | The 80 cases, with gold for every stage (see Schema). |
| `validate.py` | Checks the gold against the raw data, makes the review sample, and freezes the set. |
| `run.py` | Runs the cases through the graph and writes `results/<time>_<label>/cases.jsonl`. |
| `score.py` | Scores a run: `scored.jsonl`, `summary.json` and `report.md`. It makes no chat-model calls; the optional judge is cached. |
| `judge_prompt.yml` | The prompt for the Claude judge (Sonnet 5.5). It grades only what code can't: reworded facts and unsupported claims. |
| `common.py` | Strict SQL-result comparison and number matching, shared by all three scripts. |
| `gold_changes.md` | A log of every change to the gold, with the reason. |
| `MANIFEST.json` | Written by `validate.py --freeze`: hashes of the cases, the database and the corpus. |

## How the gold was built: answer first

Every case started from the data, not from a question:
- **SQL cases:** the reference SQL was written and run on `transactions.db` (customer `Cust_0410`).
- **Help cases:** an evidence quote was copied word for word from `data/help_documents`. Only topics that apply to any bank were used: cards, PINs, passwords, statements, alerts, ATMs and cheques.
- **Dispute cases:** a real payment inside the 120-day window was chosen, along with its expected outcome.

Only then was the user's message written around it. `validate.py` re-checks every one of these against the raw data.

| Group | Cases | What it tests |
|---|---|---|
| BankSQL | 28 | standalone questions, follow-ups, corrections, relative dates, misspelt merchants, charts, and questions the data can't answer |
| Help (RAG) | 17 | answerable questions, a near miss, topics not in the documents (must abstain), and follow-ups |
| Help vs dispute | 4 | "how do I dispute?" (help) vs "dispute my X" (dispute) |
| Guardrail | 10 | greeting, out of scope, prompt injection, SQL injection, PII, and two false-positive traps |
| Mixed intent | 8 | SQL+help, SQL+SQL, dispute+SQL, help+out of scope, three-part questions, and an over-splitting check |
| Disputes | 13 | multi-turn dialogues: happy paths for 4 reasons, close matches, a duplicate, fraud triage, 3 kinds of ineligible payment, a side question, a changed detail, and missing details |

## Running it

```powershell
# 1. Check the gold against the raw data (no model calls)
python evaluation/e2e/validate.py
# 2. Smoke test on the configured model (LLM_PROVIDER in .env; capstone for the reported numbers)
python evaluation/e2e/run.py --limit 3 --label smoke
python evaluation/e2e/score.py evaluation/e2e/results/<run>
# 3. Full run, then score with the judge (needs ANTHROPIC_API_KEY)
python evaluation/e2e/run.py --label full --delay 1
python evaluation/e2e/score.py evaluation/e2e/results/<run> --judge
# Variance across runs, on dev
python evaluation/e2e/run.py --split dev --repeat 3 --label variance
# Continue an interrupted run
python evaluation/e2e/run.py --resume evaluation/e2e/results/<run>
```

**Today's date.** "Today" is the day of the run. The app's own SQL uses SQLite `date('now')`, so the reference SQL for relative dates does too, and it is recomputed when scoring. Score a run on the day it was made; the scorer warns if you don't. The demo data ends on 2026-11-21. Run `python data/data_processing/transaction_data_enhance.py shift` if you evaluate months later, so the dispute window still covers the chosen payments. `validate.py` will report it if it doesn't.

**Isolation.** Each case runs on its own copy of `transactions.db`: old disputes, drafts and the SQL cache are cleared, and seeded disputes are applied. It also gets a new session and no Redis. The live database is never written to.

## Scoring rules

Each turn with gold is checked stage by stage, in pipeline order:

`error → guardrail → dispute_gate → context → routes → subagents → safety → sql_tool → sql_result → rag_retrieval → dispute_lookup → dispute → response_incomplete → response_unsupported`

| Bucket | First failing stage |
|---|---|
| tool_call_error | an exception, the step limit, an API error, SQL that was never written, failed or was unsafe |
| retrieval_miss | the gold evidence quote isn't among the retrieved help excerpts, or the dispute lookup missed the payment |
| reasoning_error | wrong guardrail, gate, rewrite (an entity or timeframe lost), route or sub-agent; SQL that runs but answers wrongly; a wrong dispute decision; a reply missing a key fact |
| hallucination | every earlier stage was right, but the reply states something unsupported (an invented amount, a judge-flagged claim), or answers when it should have declined or abstained |

- **SQL results** are compared strictly (`common.compare_results`). Row counts must match, every expected value needs its own cell, and year, month and date columns never count as a matching number. This fixes the false positives in the Week 1 matcher.
- **Context rewrites** are checked against a specification, not exact text: the number of sub-queries, plus the entities and timeframes they must keep (`must_keep`) or must drop (`must_not`).
- **Replies** are checked by code first: required mentions, forbidden content, and every £ amount or decimal must appear in the conversation, the SQL rows, the help excerpts or the dispute data. With `--judge`, Claude also checks reworded key facts and unsupported claims against the same evidence.
- A stage that fails while the reply is still right is counted as **recovered** rather than as a failure.
- **Tool-call errors carry a cause.** The app catches its own exceptions, so `run.py` records each turn's logged error. The scorer labels it `output_truncated`, `api_rate_limit` (the shared capstone key's concurrency cap: load, not app quality), `api_timeout`, `sql`, `step_limit` or `other`. `run.py` also stops a dialogue after 3 identical failed turns.
- **The judge fails loudly.** Any failed judge call (for example an exhausted Anthropic credit balance) is counted, shown as "N FAILED" in the report header and its warnings, and makes `score.py` exit with code 2. Those turns fall back to the deterministic checks.

## Schema (golden_cases.yaml)

```yaml
- id: SQL-09
  category: banksql_followup        # see the table above
  difficulty: medium
  split: dev                        # dev (tune on it) / test (final numbers only)
  chat_history: [{role: user, content: "..."}, {role: assistant, content: "..."}]
  turns:
  - user: and in September?
    expect:
      guardrail: SAFE               # or a list of acceptable labels
      dispute_gate: side_question   # only for turns sent while a dispute is open
      context: [{must_keep: [[groceries, grocery], [september 2026, 2026-09]], must_not: [october]}]
      context_count: [1, 2]         # acceptable numbers of sub-queries
      routes: [BankSQL]             # supervisor labels as a set; a list of lists = alternatives
      subagents: [sql_generation]   # exactly these ran (sql_generation / help_center / dispute_agent)
      sql: [{reference: "SELECT ...", compare: scalar}]   # scalar | rows | values | last_column
      rag: {answerable: true, evidence: [{doc: kb_....md, quote: "verbatim"}], key_facts: [...], optional_facts: [...]}  # optional facts are context for the judge, never required
      dispute: {status: needs_payment_confirmation, transaction_id: TX_...}
      response: {behavior: answer, must_mention: ["$sql", [alt1, alt2]], must_not: [...]}
  task: answer                      # or {type: dispute_case_raised, transaction_id, reason} ...
```

Dispute cases use `opening`, `facts` (the payment, an answer for each question id, optional scripted `replies`, `choose`, `recognises`), `expect_first`, optional `interjections` (a message sent when the agent reaches a given status) and optional `setup.existing_disputes`. The simulated customer in `run.py` answers whatever the agent asks, from `facts`.

## Before reporting numbers

1. Run on capstone. For each case where the model disagrees with the gold, recheck the gold against the raw data. Log every gold change in `gold_changes.md` with the reason.
2. Have a team member review about 25% of the cases: `python evaluation/e2e/validate.py --export-review 0.25` produces `review_sample.csv`. Set `review_status: approved` on the cases they sign off.
3. Freeze the set with `python evaluation/e2e/validate.py --freeze`. Then make the final run against the frozen set, and quote the **test** split.
