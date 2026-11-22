---
title: Reconciliation — PRD (Transaction Charts) vs Architecture Spine (FinTell baseline)
source: _bmad-output/planning-artifacts/architecture/architecture-Agentic_BChatbot-2026-11-22/ARCHITECTURE-SPINE.md
prd: prd.md
addendum: addendum.md
created: 2026-11-22
---

# Reconciliation — PRD vs Architecture Spine

Scope: places where the spine states a rule, as-is fact or deferred decision that the chart feature must respect or settle, and the PRD plus addendum neither cover nor explicitly defer it. Also PRD requirements that contradict a spine rule. Items the addendum already names (the envelope key, `agent_metrics` merging, deferred `fallback_response` ownership, AD-12 registration list, the FR-15 cache clear) are not repeated unless something about them is still missing.

Code was checked at `src/agents/text2sql_generator.py`, `src/agents/sql_execution.py` and `src/app.py` to confirm the spine facts these findings depend on.

## Summary

| # | Spine ref | Gap | Severity |
| --- | --- | --- | --- |
| G-1 | Result rows convention, structural seed, AD-4 | FR-6 assumes one result per SQL Sub-query, but the subgraph runs once per request and returns one combined result | Critical |
| G-2 | AD-5, AD-3, AD-7 | FR-5 note plus FR-10 TTFT plus FR-14 "every Reply" together fix where the chart node sits; the addendum frames it as before-or-after but doesn't record the constraints that decide it | High |
| G-3 | AD-6, AD-7 | FR-13 forbids the chart stage writing `fallback_response`, which contradicts the AD-6 stage contract as written | High |
| G-4 | AD-11, AD-8, AD-9 | The FR-15 text2sql prompt change must keep customer scoping in every subquery; this isn't stated | High |
| G-5 | AD-8, AD-1 | Who detects an "explicit chart request" (FR-5) isn't specified; guardrail/supervisor prompts don't know about charts, so UJ-4 questions might go to fallback | High |
| G-6 | AD-10 | The row cap rewrites non-aggregate SQL (adds `LIMIT 20` and `ORDER BY transaction_date DESC`) and appends a notice string; this affects FR-15 and shape detection | Medium |
| G-7 | Errors convention, AD-7 as-is | The `fallback` Skip Reason is ambiguous for as-is fallback paths (`needs_separate_questions` has no message, a leftover `query_error`, a `help_center` failure, supervisor error → no reply) | Medium |
| G-8 | Deferred: SQL cache; AD-2 as-is | Cache affects more than FR-15: failed SQL stays cached, the cache key is the supervisor's sub-query wording, and eval runs depend on cache state | Medium |
| G-9 | AD-12 (token allowlist), Streaming convention | Any chart-path LLM call inside an allowlisted node leaks tokens; the step label "Preparing a chart" shows on every request | Medium |
| G-10 | Deferred: persistence of new outputs; AD-2 | The FR-5 note is part of the Text Reply, so it is persisted to history and fed back to later turns; the PRD decides only that the Chart isn't persisted | Medium |
| G-11 | AD-15 | Plotly loads from a CDN; the UI's behaviour when Plotly is missing isn't covered | Low |
| G-12 | AD-16 | Rate limit and cost record for any new endpoint or LLM call; cost figures use Haiku rates | Low |
| G-13 | AD-11 rule 2 | Any extra data the chart needs (gap filling, re-querying) must go through `sql_execution` | Low |
| G-14 | AD-13 as-is | FR-11/SM-4 parity can't hold on the as-is divergence paths (`/process` 500 vs stream substitution) | Low |

## Findings

### G-1 — FR-6 assumes per-Sub-query results that don't exist (Critical)

- **Spine ref:** Consistency convention "Result rows" (`database_query_results` is one `list[str]`); structural seed (one `text2sql_generator → sql_execution` pass); AD-4.
- **What contradicts:** FR-6 says "with several SQL Sub-queries, the Chart comes from the **first** Sub-query (in the supervisor's order) whose result qualifies." In the as-built system, `text2sql_generator` receives the whole `banksql_queries` list and produces **one** SQL statement (`can_answer_with_single_query`). If it can't combine them, it falls back with `needs_separate_questions`. There is one `database_query_results` per request, with no per-Sub-query result to pick "the first qualifying" from. The Glossary entry "Chart … built from the result of one SQL Sub-query" has the same problem. The addendum's eval dataset asks for "a multi-sub-query question" without saying what it should produce.
- **Why it bites:** Stories for FR-6 would either need a subgraph redesign (one SQL per sub-query, per-sub-query result fields in both states under AD-4, changes to AD-5 sequencing) or can't be tested.
- **Suggested fix:** Restate FR-6 in terms of what exists: "The Chart is built from the request's single SQL result. A combined multi-Sub-query result is charted only if, as a whole, it meets FR-1/FR-2/FR-7; otherwise `unsuitable_shape`." Record per-Sub-query execution as out of scope (§6.2) or as an explicit architecture decision. Update the Glossary and the eval-dataset note to match.

### G-2 — Chart node placement is constrained more than the addendum states (High)

- **Spine ref:** AD-5 (sequential, converge at `join`); AD-3 (only `response_generator` writes the text); AD-7 (routers after failing stages check `fallback_response`).
- **What's missing:** The addendum frames this as "before or after `response_generator`". Three PRD requirements narrow it, and none of them is recorded as a constraint:
  1. **FR-5** (a one-line note when an explicit request isn't charted) needs the Chart Decision **before** `response_generator` streams, because `response_generator` is the only text writer (AD-3).
  2. **FR-10 / SM-3** (time to first token unchanged) means anything placed before `response_generator` must add almost no latency. In practice the decision must be code-only, or at least have no LLM call on the critical path.
  3. **FR-14** ("every Reply's trace includes the Chart Decision", including `fallback` and `no_transaction_data`) means the decision must be on **every** path, not only after the SQL subgraph. That puts it between `join` and `response_generator`, or after `response_generator`. If it runs after `response_generator`, the SSE `final` event is delayed, but TTFT isn't.
- **Contradiction risk:** The addendum's "favours deciding after, or in parallel to" conflicts with FR-5 if the decision comes after, and with AD-5 if it runs in parallel.
- **Suggested fix:** In the addendum, record that FR-5 + FR-10 + FR-14 together point to a code-only decision node placed `join → chart_decision → response_generator`. Rendering, or any LLM work for titles, could run after `response_generator` if needed. Or allow a split into decide-before and build-after. Say that the FR-5 note reaches `response_generator` as a state field (AD-3), not as a second writer.

### G-3 — FR-13 contradicts the AD-6 stage contract (High)

- **Spine ref:** AD-6 ("the whole body is in `try/except Exception`, which sets `state.fallback_response`"); AD-7 ("failure is signalled only through `state.fallback_response`").
- **What contradicts:** FR-13 requires that a chart-path failure "never sends the request to the fallback flow and never adds to the fallback message". Read literally, AD-6 makes the chart stage set `fallback_response` on error, and `response_generator` would then prepend that message. The addendum lists "who may write `fallback_response`" as a triggered deferred item, but doesn't say that AD-6 and AD-7 themselves need an amendment (an "optional stage" class that records failure on its own field, for example `chart_decision.skip_reason = "error"`).
- **Also missing:** FR-3/FR-13 give no Skip Reason for a chart-path error. The fixed label set has none (for example `chart_error`), yet FR-14 requires a Skip Reason in every trace.
- **Suggested fix:** Have architecture amend AD-6/AD-7 with an "optional stage" rule: catch everything, never touch `fallback_response`, and record the failure in its own output field and in metrics. Add a Skip Reason such as `chart_error` to FR-3's table, or say which existing label covers it.

### G-4 — FR-15 prompt change and customer scoping (High)

- **Spine ref:** AD-11 ("never remove or weaken the customer-scoping instructions in the text2sql prompt"; isolation is prompt-only); AD-8; AD-9.
- **What's missing:** The addendum notes that FR-15 needs a `text2sql_generator.yml` change and a cache clear. It doesn't note that end-of-period balance SQL usually needs a correlated subquery or a window, such as `MAX(transaction_date)` per month. Every inner query must also filter `customer_id`. Otherwise the "last transaction of the month" can be another customer's row, which leaks data or gives an empty or wrong result. Because isolation is prompt-only (AD-11 as-is), this is the one place a new prompt example can quietly weaken it. The same applies to any column-alias convention the chart path might add to the prompt.
- **Suggested fix:** Add to the FR-15 note: "The new prompt guidance and examples must apply `customer_id = '<customer_id>'` in every subquery or CTE. The eval set includes a balance-trend question for two customers to check isolation." Also record that any prompt change for chart-friendly output (fixed aliases such as `category`/`period`/`amount`) is an AD-11 and cache-clear event too.

### G-5 — Explicit chart request: detection, and guardrail/supervisor scope (High)

- **Spine ref:** AD-1 (routing is deterministic over state); AD-8 (prompt changes go through `AppSettings`/`Prompts`/`get_all_prompts`; the guardrail prompt is hardcoded).
- **What's missing:** FR-5 and FR-4 branch on "the user explicitly asked for a chart", but nothing says which stage produces that flag. It could be a new supervisor output field, the chart node re-reading `user_query`, or keyword code. No current prompt mentions charts. "Give me a pie chart of my spending by merchant" (UJ-4) may be classified as out of scope by the guardrail or supervisor and sent to `fallback`, which contradicts UJ-4's expected text answer. A new structured field on the supervisor output is a model change in `app_models.py` and possibly a change to the cache key.
- **Suggested fix:** Add an addendum item: "Explicit-request detection: architecture decides the producer (prefer deterministic code over `user_query`, so it adds no LLM latency). The guardrail (hardcoded) and supervisor prompts must treat chart phrasing as in scope. The eval set includes each UJ-4 phrasing to prove it isn't sent to fallback."

### G-6 — AD-10 rewrites queries in ways that affect FR-15 and shape detection (Medium)

- **Spine ref:** AD-10 (detail SELECTs capped at 20, with an optional trailing notice string; aggregate SELECTs uncapped).
- **What's missing:** The addendum says the limits "apply to uncapped aggregate results". It doesn't cover:
  - The "aggregate" test is a regex for `count|sum|avg|min|max|group_concat(`. An end-of-period balance query written with a window function (`ROW_NUMBER() OVER …`) and no aggregate counts as a detail query. It gets `LIMIT 20`, plus `ORDER BY transaction_date DESC` if it has no ORDER BY. That can error (unknown column) or truncate the series.
  - Capped results carry a trailing non-dict notice string. The shape detector must ignore it, and treat any capped result as not chartable.
  - Whether the presence of an aggregate is the sanctioned Raw Transaction List signal, or shape comes only from the result columns. Either way, the decision must not depend on SQL text the cache may have replaced.
- **Suggested fix:** In the addendum, state that the FR-15 SQL must contain an aggregate (for example `MAX(transaction_date)`), or that AD-10's detection is extended. State also that a result carrying the AD-10 notice is never charted (`raw_list`).

### G-7 — `fallback` Skip Reason vs the as-is fallback paths (Medium)

- **Spine ref:** Errors convention (`fallback_reason` values; a new reason must be handled everywhere); AD-7 as-is items.
- **What's missing:** FR-3 says `fallback` applies when "the Reply contains any fallback message". As built:
  - `needs_separate_questions` sets `fallback_response` with `fallback_message=None`. This reason is also missing from the spine's list of reasons.
  - A leftover `query_error` leaves the subgraph through `end` with the message "Attempting automatic correction".
  - A `help_center` failure reaches `response_generator` without going through `fallback`.
  - A supervisor error goes to `END` with no Reply, so no trace and no Chart Decision. That conflicts with FR-14's "every Reply", unless "no Reply" is exempt.
  
  The rule should key on `fallback_response` being set, not on the message text.
- **Suggested fix:** Redefine the `fallback` Skip Reason as "`state.fallback_response` is set, on any path", and say whether a SQL result can still be charted when only the help branch failed. State that the supervisor-error path is exempt from FR-14 until the AD-7 deferred fix lands.

### G-8 — SQL cache implications beyond the FR-15 clear (Medium)

- **Spine ref:** Deferred "SQL cache invalidation"; AD-2 as-is (SQL is cached before execution, so failed SQL stays cached); Keys convention.
- **What's missing:**
  - The cache key is `str(banksql_queries)`, so it is the supervisor's wording, not the user's. Any supervisor prompt change for G-5 also changes which SQL is reused.
  - Bad FR-15 SQL that later needs correcting stays cached. A demo question would then keep missing its chart until the cache is cleared.
  - SM-1 to SM-5 runs depend on cache state. The `query_cache` rows never expire, and the eval set needs a defined starting state.
- **Suggested fix:** Add to the evaluation section: "Run the dataset from a cleared `query_cache` and `qcache:*`, and run it twice (cold and warm) to check that the Chart Decision is stable with cached SQL." Note that a supervisor or text2sql prompt change for this feature triggers the clear.

### G-9 — Streaming allowlist and step labels (Medium)

- **Spine ref:** AD-12 (the token allowlist covers only nodes whose text is shown to the user); Streaming convention (`step`, `token`, `final`, `error` only).
- **What's missing:**
  - `/process-stream` forwards every LLM chunk from `response_generator` and `help_center`. If any chart LLM call (titles, shape classification) ran inside one of those nodes, its tokens would stream into the Text Reply.
  - A `step` event, today "Preparing a chart" via the old `visualization_agent` label, would show on every request, including UJ-3's "no chart" cases.
  - FR-10 implies no new SSE event type, but this isn't stated.
- **Suggested fix:** State that chart-path LLM calls run only in the chart node, which isn't on the token allowlist. State that the Chart travels only in `final` (no new event type). Have UX decide whether the step label shows when the decision is "no chart".

### G-10 — FR-5 note gets persisted and re-enters context (Medium)

- **Spine ref:** Deferred "whether or how new outputs are persisted with `chat_messages`"; AD-2 (the shell persists the Text Reply to Redis and `chat_messages`).
- **What's missing:** FR-12 decides that the Chart isn't persisted. The FR-5 note is part of the Text Reply, so it is saved and summarised, and `context_builder` and `history_summarizer` see it on later turns. That could make later replies mention charts, which works against UJ-3's "none of these replies mentions a chart". The Chart Decision itself isn't persisted anywhere, not even in `request_metrics`.
- **Suggested fix:** Decide explicitly whether the note is persisted as part of the text (accepted) or removed before saving. State whether the Chart Decision is persisted in `request_metrics` for later analysis, or is trace-only.

### G-11 — Plotly CDN unavailable (Low)

- **Spine ref:** AD-15 (optional services degrade; consumers null-check the handle).
- **What's missing:** FR-13 covers malformed chart data in the UI. It doesn't cover Plotly.js (CDN) failing to load, which is a real case for an offline local demo.
- **Suggested fix:** Add a consequence to FR-13: "If the chart library is unavailable, the Text Reply shows with no Chart and no console-breaking error." Optionally, vendor Plotly locally.

### G-12 — Rate limit and cost record (Low)

- **Spine ref:** AD-16 (every graph-invoking endpoint carries `20/minute`; `request_metrics` is the only cost record; Haiku rates are as-is).
- **What's missing:** If architecture adds a chart endpoint (for example to fetch the chart after the stream for FR-10), it needs the limiter and parity under AD-13. SM-3 measures only latency; any chart LLM cost is recorded at Haiku rates, so the figures are only indicative.
- **Suggested fix:** Add one line to the addendum: "No new endpoint is expected. If one is added, it follows AD-16 and AD-13. Chart LLM cost figures are indicative (AD-16 as-is)."

### G-13 — No side-channel SQL (Low)

- **Spine ref:** AD-11 rule 2 (SQL derived from user input runs only through `sql_execution`); AD-14 (shared connection).
- **What's missing:** Open Question 3 (gap filling) and FR-15 could lead to the chart stage running its own query, for example to fetch missing months or the last balance. This isn't ruled out.
- **Suggested fix:** State that the chart path only reads `database_query_results`, and that any gap filling is done in code.

### G-14 — Parity on the as-is divergence paths (Low)

- **Spine ref:** AD-13 as-is (with no reply, `/process` returns 500 while `/process-stream` substitutes text and persists it; exceptions become an SSE `error`).
- **What's missing:** FR-11 and SM-4 target 100% parity "for each question in the evaluation dataset". On those as-is paths the envelopes already differ.
- **Suggested fix:** Exclude the AD-7/AD-13 as-is paths from SM-4, or fold the deferred fix into this feature along with the shared post-graph helper the addendum already suggests.

## Checked and covered (no gap)

- Envelope key and reuse of `visualization`: addendum, FR-11.
- `agent_metrics` merging: addendum (deferred item named).
- AD-12 registration points: addendum. The only open choice is whether to reuse the `visualization_agent` id or add a new one. AD-12 forbids new aliases, so whichever is chosen must be one id everywhere.
- The AD-10 cap applying to raw lists: FR-3 `raw_list`.
- Direction and money convention: FR-8.
- No schema change: §5. FR-15 is a prompt change only, and the addendum names the cache clear.
- `tests/data/` holds data only: addendum.
