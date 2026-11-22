---
title: Reconcile review — Transaction Charts inputs vs ARCHITECTURE-SPINE (AD-17..AD-24 + amendments)
reviewed: ARCHITECTURE-SPINE.md (draft, 2026-11-22)
inputs: prd.md, addendum.md, reconcile-architecture-spine.md (G-1..G-14)
code checked: src/agents/sql_execution.py, src/agents/text2sql_generator.py, src/agents/response_generator.py, src/agents/fallback.py, src/agents/guardrails.py, src/app.py, src/index.html, src/clients/sqllite_db_creation.py
created: 2026-11-22
mode: read-only (spine not edited)
---

# Reconcile review: Transaction Charts

## Verdict

Nearly every PRD requirement and G-item is covered by the spine. The placement, optional-stage, envelope, persistence, streaming and no-side-channel decisions all match the inputs. **One real gap:** the AD-20 classification can't produce the Skip Reasons the PRD's own examples expect for single totals and plain transaction lists. The spine also claims that cold-cache and warm-cache runs give the same Chart Decision, which the as-is cache doesn't guarantee. The deliberate `too_many_*` deviation is sound. However, three passages in the PRD inputs still say the opposite and should be amended.

## Findings

| # | Severity | Area | Summary |
| --- | --- | --- | --- |
| R-1 | High | FR-3 precedence vs AD-20 | Single totals and two-value answers land on `unsuitable_shape` (or `raw_list`), not `too_few_points` |
| R-2 | High | FR-3 row 5, UJ-3 | "One row per transaction" has no deterministic test; "last 10 transactions" without `transaction_id` lands on `unsuitable_shape` |
| R-3 | Medium | SM run rule, AD-20 | The claim that cold and warm runs match is too strong: failed-then-corrected SQL stays cached, and the cache key is LLM wording |
| R-4 | Medium | Glossary deviation | `too_many_*` for capped convention results is sound, but the PRD glossary, the addendum and G-6 still say `raw_list` |
| R-5 | Medium | FR-8 title and axis | A single `category` alias loses the grouping (merchant or category), and optional `period_start/end` lets the title omit the period |
| R-6 | Medium | FR-5 / SM-1 | The explicit-request matcher is narrower than the PRD glossary (a named type on its own) |
| R-7 | Low | FR-2 / FR-1 | Code-side sorting, ISO validation and distinct-label checks are implied but not stated |
| R-8 | Low | SM-5 | A global `CHART_FAULT_INJECTION` flag doesn't fit a single labelled dataset run |
| R-9 | Low | Non-goals / SM-C2 | The alias convention changes the SQL for every comparison and trend question, not only FR-15 |
| R-10 | Low | FR-13 / Plotly | The Plotly `<script>` in `<head>` blocks rendering when the CDN is unreachable |
| R-11 | Low | FR-14 trace panel | `index.html` renders trace detail only for `visualization_agent`, and nothing says how `chart_builder` fields display |
| R-12 | Low | FR-15 tie-break | `transaction_id` is `TEXT`, so the tie-break is deterministic but lexical (`T10` < `T9`) |
| R-13 | Low | Evaluation convention | The two-customer FR-15 row and the cache-clear prerequisite are only partly in the spine's Evaluation convention |

### R-1 — Single-total results can't reach `too_few_points` (High)

- **PRD:** FR-3 row 7 is "Fewer than 3 Data Points (a single total, two values)". UJ-3 "What's my current balance?" (one value) expects no Chart. Its Skip Reason in the dataset will be labelled `too_few_points`, because that is the PRD's own example. SM-6 measures label matches (≥ 90%).
- **Spine:** AD-20 applies the column convention only "when a transaction answer is a comparison or a trend". A single total isn't one, so the prompt won't alias it: `SELECT SUM(amount_usd) AS total_spent …`. AD-20's classification then says that a result not fitting the convention is `raw_list` if it has `transaction_id` or one row per transaction, and `unsuitable_shape` otherwise. So:
  - "Total spend last month" (one aggregate row, no label column) → `unsuitable_shape`.
  - "Current balance" (`SELECT balance … ORDER BY transaction_date DESC LIMIT 1`, one row taken from one transaction) → `raw_list` or `unsuitable_shape`, depending on how "one row per transaction" is read.
  - "Income vs spending this month" (one row, two value columns): mixed direction, so `unsuitable_shape` beats `too_few_points` on FR-3 order. The PRD's "two values" example is ambiguous.
- **Result:** in practice `too_few_points` is only reachable for convention-shaped results with 1 or 2 rows, for example spending by category when the customer has two categories. Under FR-3 table order, row 6 (`unsuitable_shape`) catches every degenerate single total before row 7 can.
- **Fix:** add an explicit degenerate-series rule to AD-20, evaluated before row 6. For example: "A non-capped result with 1–2 rows, no `transaction_id`, and at most one label column plus one numeric column (any names) is a series of N < 3 Data Points and reports `too_few_points`. A one-row result with two or more numeric columns reports `too_few_points` only if it has a single direction; otherwise it reports `unsuitable_shape`." Alternatively, extend the convention so that scalar answers are aliased `spending` / `income` / `net` / `balance` with no label column, and treat a missing label column with ≤ 2 rows as `too_few_points`. Then list the expected Skip Reason for each UJ-3 question in the dataset, so the labels and the rules agree.

### R-2 — "One row per transaction" has no deterministic test (High)

- **PRD:** FR-3 row 5 says `raw_list` applies "even if it has 3–12 dated rows". UJ-3 "Show my last 10 transactions" should be a Raw Transaction List. SM-1 needs 100% "no chart", and SM-6 needs the Skip Reason to match.
- **Spine:** AD-20 says `raw_list` applies "if it has `transaction_id` or one row per transaction". The rules can't see SQL (AD-20 forbids reading it) and have no `customer_id` or count to infer "one row per transaction" from. A typical detail query selects `transaction_date, merchant_brand_name, amount_usd, transaction_type`, with no `transaction_id`. That makes it `unsuitable_shape`, and the outcome depends on whether the LLM happened to include the id column.
- **Second risk:** if the LLM aliases a detail query to the convention (`transaction_date AS period, amount_usd AS spending`), the result becomes a 10-point Line Chart. That breaks SM-1, which targets 100%. The spine doesn't require labels to be distinct (see R-7). With distinct labels enforced, per-transaction rows would usually still slip through when all dates differ.
- **Fix:** define `raw_list` by column presence. A result is `raw_list` when it has any raw transaction column (`transaction_id`, `transaction_date`, `transaction_type`, `card_number`, `amount_usd` unaliased, `merchant_brand_name` unaliased) and does not fit the convention. Add to the text2sql prompt that detail and list answers must never use the chart aliases, and include a dataset row for "last 5 payments" with 3–12 rows.

### R-3 — Cold-run and warm-run stability is claimed, not guaranteed (Medium)

- **PRD:** §7 requires the same Chart Decision on the cold run and the warm run.
- **Spine:** AD-20 says "Because the convention lives in the SQL, it is cached with the SQL, so cold and warm runs give the same Chart Decision." Two as-is facts, both recorded elsewhere in the spine, break this:
  1. AD-2 as-is: `text2sql_generator` caches SQL **before** execution (`save_cached_sql` at `text2sql_generator.py:108`). A question whose first SQL failed and was corrected therefore replays the failed SQL warm. Correction then runs again through an LLM, which may produce different SQL, a different result, and possibly a leftover `query_error`, which reports `fallback`.
  2. The cache key hashes `str(banksql_queries)`, which is the supervisor's LLM output, normalised only for case and whitespace (`_make_cache_key`). If the supervisor words a sub-query differently on the warm run, the cache misses and fresh SQL is generated. AD-21's "strip presentation words" step adds more LLM variance to that key.
- **Fix:** either add a **Chart feature** line to AD-2 that moves the cache write to after a successful `sql_execution` (and caches the corrected SQL), or weaken the AD-20 claim and make the runner record a cache hit or miss and `sql_query_correction` per question. Report cold/warm mismatches separately, and exclude rows that went through correction from the stability check.

### R-4 — The `too_many_*` deviation is sound, but the PRD text contradicts it (Medium)

- **Assessment: the deviation is sound.** AD-10 caps any result over 20 rows, aggregates included (verified in `sql_execution.py`, which truncates every result and appends the notice). A capped convention-shaped result must have had more than 20 points. That exceeds both the 8 and the 12 limits, so `too_many_categories` or `too_many_time_points` is the true reason, and the FR-5 note ("too many … to chart clearly") is then accurate. Calling it `raw_list` would make the trace and the user-facing note wrong.
- **The PRD supports the deviation in places:** UJ-2's edge case (daily spending, about 30 Time Points), FR-2 ("daily rows across a whole month … no Chart") and FR-15 ("daily across a month gets no Chart, as FR-2 requires") all read as a too-many-points case. Under the glossary those Results are capped, so the PRD contradicts itself, and the spine picks the reading the requirements intend.
- **PRD text that still conflicts:**
  - The glossary entry for Raw Transaction List includes "any Result that the row cap has truncated".
  - The addendum, under "FR-15 … row cap", says "A Result that carries the AD-10 notice string is a Raw Transaction List and is never charted."
  - G-6's suggested fix says "a result carrying the AD-10 notice is never charted (`raw_list`)".
  - FR-3 row 5 is placed before rows 8 and 9, so under the glossary a capped result stops at row 5.
- **Also:** the spine doesn't mark this as a deviation from the PRD. AD-10 and AD-20 state it as fact. Whoever labels the dataset will follow the glossary and label capped aggregates `raw_list`, which costs SM-6.
- **Fix:** add a one-line "Deviates from PRD glossary (Raw Transaction List): a capped convention-shaped result reports `too_many_*`; PM to amend the glossary and the addendum" to AD-20. Ask the PM to change the glossary to "…including any non-convention Result that the row cap has truncated". Label the UJ-2 daily-spending row `too_many_time_points`.

### R-5 — FR-8 axis label and title can't be fully derived in code (Medium)

- **PRD:** FR-8 requires a title that names the measure **and the period** ("Spending by category — October 2026"), and a category axis "labelled with the Category grouping".
- **Spine:** AD-20 has one label alias, `category`, whatever was grouped (merchant brand, category1 or category2). The rules can't read SQL. So the code can't tell "by merchant" from "by category", for the x-axis or for the title. `period_start` and `period_end` are **optional**, so a bar title may have no period at all. Line titles can use the first and last `period`, which works. A side effect on tone: the response LLM sees `category: 'Starbucks'` and may call merchants "categories" (SM-C2).
- **Fix:** split the label alias into `merchant` / `category` / `subcategory`, which stay in the convention constants. Alternatively, add a constant `grouping` column. Make `period_start` and `period_end` required for category comparisons, or define the fallback title with no period ("Spending by merchant") and accept it as meeting FR-8.

### R-6 — The explicit-request matcher is narrower than the PRD glossary (Medium)

- **PRD glossary:** "chart", "graph", "plot", "visualise"/"visualize", **or a named chart type ("bar", "line", "pie"…)**. SM-1 requires the FR-5 note exactly when there is an Explicit Chart Request, so labels will follow the glossary.
- **Spine AD-21:** a named type counts only when "followed by 'chart' or 'graph'". That makes the named-type rule redundant, because "chart" alone already matches. "Show it as a pie", or "bar it by category", then gets no note, against the dataset label. Inflections ("charts", "graphs", "plotting", "visualisation") aren't specified.
- **Assessment:** the narrowing is sensible. Bare "bar" and "line" would match "Barclays" with a careless pattern, and "line" or "timeline" too. But it is an undeclared deviation.
- **Fix:** either match `\b(pie|bar|line|scatter|donut|area)\b` when it comes after "as a" or "a … of", plus inflected forms of chart, graph, plot and visualis/ze, or mark AD-21 as a deviation and have the PM amend the glossary. Include a bare "as a pie" phrasing in the UJ-4 dataset rows.

### R-7 — Ordering, ISO-format and distinct-label checks are implied, not stated (Low)

- FR-2 requires time order "whatever order the rows came back in". AD-20 makes the SQL end in `ORDER BY period`, and AD-19 says points "are already in plot order". Neither says that `chart_rules` itself sorts by `period`. Requiring the code to sort would remove the dependence on the LLM's SQL.
- Nothing says what happens when `period` values fail the ISO pattern. `strftime('%m')` gives `"10"` with no year, which collides across years. Treat any such value as `unsuitable_shape`.
- Duplicate `category` or `period` values break the definition of a Category or Time Point as one distinct value. Classify a result with duplicate labels as `raw_list` or `unsuitable_shape`.
- `database_query_results` being `None` on the SQL path with no fallback (a non-SELECT, or an empty query, `sql_execution.py:43`) isn't mapped to a reason. Map it to `empty_result`.

### R-8 — Fault injection vs the labelled dataset (Low)

The `chart_error` row in the dataset needs a failure on one question, but `CHART_FAULT_INJECTION` is a process-wide setting that `/update-settings` doesn't toggle. State that the runner runs the injected rows in a separate pass with the app restarted with the flag set, or key injection on a sentinel in `user_query`. SM-5 also needs a baseline Text Reply to compare against. LLM text isn't deterministic, so define "unchanged" as a non-empty reply with no chart or error text.

### R-9 — The alias convention widens the "no change to Text Replies" non-goal (Low)

§5 allows only two text changes: the FR-5 note and FR-15. AD-20 renames output columns for **every** comparison and trend query, and ISO `period` values ("2026-10") reach the rephrase LLM. AD-21 also rewrites the supervisor's `sub_query` to strip chart words, which changes the cache keys. The addendum anticipated the alias change, so this is not a contradiction, but SM-C2 should explicitly cover it. Add a prompt line telling the rephrase to write periods as month names.

### R-10 — Plotly missing (Low)

AD-15 and AD-23 cover the requirement: check that Plotly is loaded, render nothing, throw nothing, and render the Text Reply first. However, `index.html:10` loads Plotly with a synchronous `<script>` in `<head>`. On a hung, offline CDN this delays the whole page, and the delay can exceed the FR-13 "page still works" intent. Add `defer` or `async`. The Plotly upgrade or vendoring is correctly deferred.

### R-11 — How the trace panel shows the decision (Low)

FR-14 requires the decision in "the existing UI trace panel". `index.html:2337` has a detail block only for `visualization_agent`. That block shows `chart_type`, `chart_title` and `reasoning`. AD-12 and AD-22 remove that id and add a `chart_builder` icon and trace entry, but don't say the panel renders `decision`, `skip_reason` and `explicit_request`. Add that to AD-22, or to the files-touched list.

### R-12 — FR-15 tie-break (Low)

`transaction_id` is `TEXT` (`sqllite_db_creation.py:62`), so ordering by `(transaction_date, transaction_id)` is deterministic, which meets the PRD. However, it is lexical, so it may not pick the row whose running `balance` is truly last within a day. The PRD lets architecture define the tie-break, so this isn't a contradiction. Note the risk, or use `rowid` (insertion order from the xlsx) if that matches the running-balance order.

### R-13 — Evaluation convention gaps (Low)

The spine's Evaluation convention covers the location, the runner and the cold-then-warm runs. It doesn't list the addendum's coverage items: the two-customer FR-15 row, both sides of each limit, a mixed SQL-and-help question, a multi-sub-query question, mixed direction, and pie-to-bar substitution. It also doesn't say that labels go stale after a `shift`. Point to the addendum list. For FR-15 isolation, have the runner check that the two customers' series differ, or match a reference SQL query per customer.

## Requirement-by-requirement check

| Item | Status | Where |
| --- | --- | --- |
| FR-1 bar, 3–8, no top-N | Landed | AD-20 limits via FR-3, §5 via AD-20 "never regroup" |
| FR-2 line, 3–12, time order, no gap fill | Landed with a gap (R-7: sorting in code, ISO validation) | AD-20, AD-19 |
| FR-3 table and precedence | Partly landed: order and labels are fixed (AD-19, conventions), but R-1 and R-2 make rows 5 and 7 unreachable as the PRD expects | AD-19, AD-20 |
| FR-3 AD-7 supervisor exemption | Landed | AD-7 as-is |
| FR-4 bar/line only, substitution not mentioned | Landed (the matcher ignores type; supervisor strips presentation words so the rephrase doesn't mention a pie) `[ASSUMPTION]` | AD-21 |
| FR-5 note: explicit only, not for `chart_error`, persisted, fixed wording | Landed; the matcher is narrower than the glossary (R-6) | AD-21, AD-3 |
| FR-5 in scope, not sent to fallback | Landed as an assumption: supervisor prompt changed, guardrail unchanged. The guardrail prompt has no out-of-scope class (verified), so the risk is low | AD-21 |
| FR-6 one combined Result, help never charted | Landed | AD-20 inputs, AD-5 |
| FR-7 single series | Landed (exactly one label column and one value column) | AD-20 |
| FR-8 title, axes, USD, one direction, negatives | Partly landed (R-5); direction comes from the value column name | AD-20 |
| FR-9 placement, theme, 1280 px | Theme and redraw landed (AD-23); the 1280 px fit isn't mentioned (UI detail) | AD-23 |
| FR-10 TTFT, Chart only in `final`, no new event, note not streamed | Landed. Verified: `index.html` replaces the bubble with `payload.response` from `final` | AD-17, AD-21, AD-22 |
| FR-11 parity, same empty value | Landed (`visualization: null`, shared helper) | AD-13, AD-22 |
| FR-12 not persisted, no placeholder | Landed | AD-22, AD-23 |
| FR-13 never breaks the Reply, no fallback write, UI safe, Plotly missing | Landed (R-10 minor) | AD-18, AD-15, AD-23 |
| FR-14 decision in the trace on every path, metrics for any LLM use | Landed in the backend; UI rendering unspecified (R-11). No LLM, no metrics entry (AD-24) | AD-12, AD-22 |
| FR-15 end-of-period balance, tie-break, customer filter in every subquery or CTE, cache clear | Landed (R-12 minor) | AD-11, AD-20, runbook |
| SM-1 / SM-6 | At risk from R-1, R-2, R-4 and R-6 | — |
| SM-2 | Supported | — |
| SM-3 | Supported (code-only stage) | AD-17 |
| SM-4 | Landed, with the as-is exclusions | AD-13 |
| SM-5 | Mechanism exists; how the run works is unspecified (R-8) | AD-18 |
| SM-C1 / SM-C2 | Not mentioned; the runner should report them. See R-9 | — |
| Cold/warm stability | Overclaimed (R-3) | AD-20 |
| UJ-1 / UJ-2 | Landed; the UJ-2 daily edge case relies on the R-4 deviation (good) | — |
| UJ-3 | No-chart outcome lands; Skip Reasons fall to R-1 and R-2 | — |
| UJ-4 | Landed (R-6 phrasing caveat) | AD-21 |
| Non-goals | Landed; R-9 note | AD-17, AD-20, AD-11 |
| Unused visualization code | Removed (models, state field, label, trace, icon, `plotly_json` path, Python `plotly`) | Seed, Stack |
| G-1 combined Result | Landed (FR-6 already restated) | AD-20 |
| G-2 placement | Landed | AD-5, AD-17 |
| G-3 optional stage, `chart_error` | Landed | AD-6, AD-7, AD-18 |
| G-4 customer scoping | Landed | AD-11, AD-20 |
| G-5 explicit-request producer, in-scope prompts | Landed (code matcher in `chart_builder`; supervisor prompt) | AD-21 |
| G-6 row cap | Landed: the corrected AD-10 as-is and `ORDER BY period` avoid the injected `ORDER BY transaction_date`. G-6's "must contain an aggregate" isn't needed once the query ends in `ORDER BY` (verified: only `LIMIT 20` is appended, and 20 rows is already past the 12-point limit, so no chartable series is cut). Its `raw_list` part is superseded (R-4) | AD-10, AD-20 |
| G-7 `fallback` keyed on `fallback_response` | Landed | AD-20 inputs, Deferred |
| G-8 cache | Partly landed (runbook, Keys convention); R-3 | AD-2, AD-20 |
| G-9 streaming | Landed (silent label as `[ASSUMPTION]`, not on the allowlist, `final` only) | AD-12, AD-22 |
| G-10 note persisted, decision not in `request_metrics` | Landed | AD-21, AD-22, AD-16 |
| G-11 Plotly CDN | Landed (R-10 minor) | AD-15, AD-23, Deferred |
| G-12 no endpoint; indicative cost | Landed | AD-16 |
| G-13 no side-channel SQL | Landed | AD-11, AD-20 |
| G-14 parity exclusions | Landed | AD-13 |
