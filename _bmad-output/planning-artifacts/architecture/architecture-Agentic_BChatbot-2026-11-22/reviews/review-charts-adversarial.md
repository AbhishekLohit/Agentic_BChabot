---
title: Adversarial review — Transaction Charts ADs (AD-17..AD-24)
target: ARCHITECTURE-SPINE.md (draft, 2026-11-22)
lens: "two stories, each obeying every AD to the letter, that still build incompatibly"
reviewer: adversarial (read-only)
date: 2026-11-22
---

# Adversarial review — Transaction Charts

**Verdict:** The chart ADs settle *where* things live and *who writes what field*, but leave the **data shapes inside those fields** (period strings, label/value types, the trace entry, the note table) and the **classification of non-convention results** open. That gap is enough for the chart_rules, prompt, trace, UI and eval-runner stories to each follow the spine and still miss SM-1 (100%) and SM-6. Most holes are closed by adding a few concrete tables to AD-19, AD-20, AD-21 and AD-22, plus one new AD for the evaluation harness.

Evidence came from the code and the live DB (read-only): `transactions.year` and `month` are `INTEGER`. Only `Cust_0410` has rows, and its data runs 2023-05-16 to 2026-06-13. `HISTORY_LOOKBACK_HOURS=24` seeds every new session. The rate limit is `20/minute` per IP. `_build_agent_trace` emits `{agent,title,status,summary,details,metrics}` with `status ∈ ok|skipped|fallback`. The UI gates rendering on `should_visualize && plotly_json`.

Ranked by likelihood of actually biting. Each hole lists the two clashing units, why both are AD-compliant, what breaks, and the tightening that closes it.

---

## H1 — The chart_builder trace entry has no shape, and the eval runner has nothing to read · **Critical**

**Units:** envelope/trace story (`_build_agent_trace`) vs eval runner story (`scripts/eval_charts.py`) vs UI trace-panel story (`buildDetails` in `index.html`).

**Why each one complies:** AD-22 says only that `_build_agent_trace` "adds a `chart_builder` entry showing the decision and the skip reason". The trace dev writes `{"status":"skipped","summary":"No chart — too_many_categories","details":{"reason":"too_many_categories"}}`. The runner dev parses `details.skip_reason` and `details.decision`. The UI dev renders `d.chart_type` and `d.chart_title`, copying the old `visualization_agent` block. All three are compliant.

**Breaks:**
- SM-6 reads `None` for every row.
- SM-2 cannot tell `bar` from `line` without parsing `visualization`.
- The UI trace panel shows nothing.
- A `chart_error` entry gets `status:"fallback"` (red) from one dev and `"skipped"` (grey) from another.
- The runner cannot tell "the entry is missing because of the AD-7 supervisor-END exemption" from "the trace builder forgot it".

**Second-order clash:** the runner needs to know whether the FR-5 note was appended (SM-1). Its only options are both bad:
- re-derive `explicit && none && !chart_error` itself, which gives the rule a second owner, or
- regex the reply for note strings it doesn't own (see H5).

**Tighten AD-22 with a fixed entry:**
```
{ "agent": "chart_builder", "title": "Chart",
  "status": "ok" (bar|line) | "skipped" (none, any reason incl. chart_error),
  "summary": "<bar|line> chart, N points" | "No chart: <skip_reason>",
  "details": { "decision": "bar|line|none", "skip_reason": SkipReason|null,
               "explicit_request": bool, "note_appended": bool,
               "point_count": int|null, "title": str|null },
  "metrics": null }
```
- Position: immediately before `response_generator`.
- `note_appended` is written by `response_generator` onto state (for example `chart_response.note_appended`, or a sibling field). It is never recomputed by the trace builder.
- The runner reads Chart Decision, Skip Reason and note presence **only** from this entry, and chart type **only** from `visualization.chart_type`. A missing entry counts as "exempt (AD-7)" only when `response_generation_response` is also absent. Otherwise the row fails.

---

## H2 — `period` formatting: the ISO-string convention is not producible as written, and two plausible chart_rules implementations disagree · **Critical**

**Units:** text2sql prompt story vs chart_rules story vs UI renderer story.

**Why each one complies:** AD-20 says `period` values are lexically sortable ISO strings. But `year` and `month` are `INTEGER`, and the existing prompt examples group by `year, month` and filter with `year = 2024` or `year = strftime('%Y','now')`. So an LLM that follows the prompt's own examples produces one of these:
- `year || '-' || month` gives `'2026-3'`. It is not ISO, and it sorts after `'2026-10'`.
- `year AS period` gives the int `2026`.
- `strftime('%Y-%m', transaction_date)` gives `'2026-03'`, which is correct.

**chart_rules dev A** validates the regex `^\d{4}(-\d{2}(-\d{2})?|-W\d{2})?$`. Otherwise it reports `unsuitable_shape`.
**chart_rules dev B** sorts whatever arrives and builds `ChartPoint(label=row["period"])`. AD-19 types `label: str`, and Pydantic v2 does **not** coerce `int → str`, so an int year raises, gets caught, and becomes `chart_error`. `'2026-3'` sorts wrong and draws a zig-zag line. FR-2 says "time order whatever the row order", so this is a silent correctness failure.

**UI clash:** the UI dev hands `points[].label` straight to Plotly as `x`. Plotly auto-types `'2026-03'` and `'2026-03-05'` as a **date axis**, which spaces irregular months by calendar time and shows gaps that FR-2 says aren't filled. `'2026-W05'` and `'2026'` stay category axes. So the same feature draws different axis semantics by granularity. A second UI dev "prettifies" labels to `Mar 2026` in JS while chart_rules has already prettified them. The result is double formatting or unparsable labels.

**Tighten AD-20 and AD-19:**
- **Prompt (AD-20):** `period` is produced **only** by `strftime` on `transaction_date`, with one fixed form per grain:
  - year: `strftime('%Y',…)`
  - month: `strftime('%Y-%m',…)`
  - week: `strftime('%Y-W%W',…)` (or `%G-W%V`, but pick one)
  - day: `transaction_date`
  - Never build it from `year` or `month`. Add one example per grain.
- **chart_rules (AD-20):**
  - Validate every `period` against the four regexes. A non-string, a non-match, mixed grains or a duplicate label gives `unsuitable_shape`. It never gives `chart_error`, and labels are never coerced.
  - Sort on the raw ISO string.
- **ChartSpec (AD-19):** `ChartPoint.label` is the **display** label, produced by chart_rules from a fixed table:
  - `YYYY` → `2026`
  - `YYYY-MM` → `Mar 2026`
  - `YYYY-Www` → `Wk 05 2026`
  - `YYYY-MM-DD` → `5 Mar 2026`
  - Sort happens before formatting, and the UI never reformats.
- **UI (AD-23):** always set `xaxis.type = 'category'`, and keep the point order exactly as sent.

---

## H3 — FR-3 order vs AD-20 classification: four result shapes have no single owner · **High**

**Units:** chart_rules story vs eval-dataset story (labels written from the PRD).

AD-20 classifies only two cases: "doesn't fit the convention → `raw_list`/`unsuitable_shape`" and "convention + AD-10 notice → `too_many_*`". The dataset author labels from the PRD. They collide on these cases:

| Result (real output of today's prompt) | chart_rules per AD-20 | Dataset label per PRD | Collision |
| --- | --- | --- | --- |
| `[{'current_balance': 1234.5}]` ("What's my current balance?", UJ-3) | not convention, no `transaction_id` → `unsuitable_shape` | FR-3 row 7 "a single total" → `too_few_points` | SM-6 miss on every scalar question |
| `[{'total_spent': None}]` (SUM over zero rows; **every "last month" question today**, because the data ends 2026-06-13) | `unsuitable_shape` (dev A), `empty_result` (dev B: "all values null"), or `chart_error` (dev C: `float(None)` raises) | `empty_result` | Three-way split |
| Convention-shaped result, 21+ rows (30 daily points), capped with the notice | `too_many_time_points` (AD-10 "Chart feature" line) | PRD glossary: a Result "that the row cap has truncated" **is** a Raw Transaction List → `raw_list` | Spine contradicts the PRD glossary |
| Convention columns, but two rows share a label, or a value is `None` | unspecified: count distinct labels, reject, or `ChartPoint(value=None)` raises → `chart_error` | `unsuitable_shape` | `chart_error` is reserved for real failures (FR-13), so this also corrupts SM-5 |
| Non-convention rows **without** `transaction_id`, e.g. `SELECT transaction_date, amount_usd, merchant_brand_name …` | "one row per transaction" can't be decided from rows without reading SQL, which AD-20 forbids | `raw_list` | Undecidable as written |

**Tighten AD-20 with an explicit decision table** that chart_rules implements and the dataset author labels from:
1. `database_query_results` is `[]` (after dropping the notice), or every row has every value `None` → `empty_result`.
2. Raw: any row has a raw-schema column (`transaction_id`, `transaction_date`, `amount_usd`, `card_number`, `transaction_type`), or the result is non-convention and capped → `raw_list`.
3. Non-convention, ≤ 2 rows, every column scalar → `too_few_points`. Otherwise non-convention → `unsuitable_shape`.
4. Convention-shaped, but with a duplicate label, a `None` or non-finite value, a non-numeric value, mixed period grains, or `balance` paired with `category` → `unsuitable_shape`.
5. Convention-shaped and capped → `too_many_categories` or `too_many_time_points`.
6. Then the 3–8 and 3–12 counts.

Also amend the PRD glossary so that "truncated" means a truncated **non-aggregate** list. Record this in the reconcile file, or SM-6 is unreachable by construction. Values are accepted only as `int` or `float`, never `str`. Pydantic's lax `"12.5" → 12.5` coercion must not be what decides the outcome.

---

## H4 — The explicit-request matcher: the spine's word list, the PRD glossary and the dataset disagree · **High** (SM-1 is 100%)

**Units:** chart_builder or chart_rules story vs eval-dataset story.

**Why each one complies:** AD-21 says "standalone 'chart', 'graph', 'plot', 'visualise'/'visualize', or a named chart type followed by 'chart' or 'graph'".
- **Dev A** writes `\b(chart|graph|plot|visuali[sz]e)\b`. It misses `charts`, `graphs`, `plotted`, `plotting`, `charting`, `visualisation`, `visualization` and `visualized`.
- **Dev B** writes a prefix match `\b(chart|graph|plot|visuali[sz])`. It catches those, but also `Chartered` and `graphic`. Live merchant names include `Ivory Bar And Grill` and `Istanbul Bar`. They don't match today, but they show the merchant vocabulary overlaps the chart-type vocabulary.
- **The PRD glossary** counts a named chart type on its own ("bar", "line", "pie"…) as explicit, so "show it as a pie" is explicit for the dataset author and not explicit for AD-21.
- **Negation** ("no chart please, just the total") is explicit under both regexes, so the user who declined a chart gets a "no chart was shown because…" note.

Every one of these differences flips the FR-5 note and fails an SM-1 row.

**Tighten AD-21:**
- Pin the matcher as a named constant `EXPLICIT_CHART_PATTERN` in `utils/chart_rules.py`, with the exact regex: case-insensitive, word-initial, covering the inflections `chart(s|ed|ing)?`, `graph(s|ed|ing)?`, `plot(s|ted|ting)?`, `visuali[sz](e|ed|ing|ation)`, and `(bar|line|pie|donut|doughnut|scatter|area|column|histogram)\s+(chart|graph|plot)`.
- Decide the bare "pie" case explicitly, and amend the glossary to match.
- Ship a ≥ 15-case positive/negative table in the AD, including `Standard Chartered`, `graphic`, `bar and grill`, `no chart`, `charts` and `visualisation`. The dataset reuses that table verbatim.
- Decide negation: either "a negated mention is not explicit" or "out of scope; the note is acceptable", and label the dataset to match.

---

## H5 — No single owner of the note: strings, placement and the LLM's own chart talk · **High**

**Units:** response_generator note story vs eval runner vs supervisor/text2sql prompt story.

1. **String location.** AD-21 puts the fixed strings "in `response_generator`". The runner (H1) or the dataset needs them to check SM-1. A runner dev copies the dict, so there are now two owners and they drift. **Fix:** put `CHART_NOTES: dict[SkipReason, str]` in `utils/chart_rules.py` next to `SkipReason`. It must have a total mapping over the eight non-error reasons, checked by an assert at import. `response_generator` imports it, and the runner relies on `details.note_appended`, not string matching.
2. **Placement.** Today's `response_generator` has three independent blocks: fallback, help, then `if database_query_results is not None`. A dev can put the note inside the SQL block (the natural spot next to the rephrase) without breaking AD-3's order rule. That leaves explicit requests on the help-only (`no_transaction_data`), guardrail or off-topic (`fallback`) and `empty_result`-with-None paths with no note, and SM-1 fails. **Fix:** AD-21 states that the note is appended after all three blocks and outside them, unconditionally on path.
3. **The LLM talks about charts.** AD-21 relies on the supervisor *LLM* stripping presentation words from `sub_query`, and `response_generator` feeds `banksql_queries` to the rephrase LLM. When the strip fails (an `[ASSUMPTION]`), the rephrase says "here is a pie chart…" or "I can't draw charts", and that sits next to a deterministic note that contradicts it or repeats it. The `responsegenerator` prompt is not in the "changed" list. **Fix:** add one line to the response-generator prompt ("Never mention charts, graphs or visuals; the UI handles them"). Update AD-8's line "the chart feature adds no prompt" so it reads "changes three prompt texts". Optionally, have chart_rules strip the presentation words deterministically before they reach any prompt.
4. **Wording precision.** The PRD's example note, "too many *merchants*", needs the grouping dimension, which a per-`SkipReason` string can't know (see H6). Either accept generic wording ("too many categories to chart clearly") in the PRD example, or key the table by `(skip_reason, grouping)`.
5. **`fallback_message=None`.** `needs_separate_questions` sets it, and `"\n".join` then raises inside `response_generator`. A note dev who filters `None` while touching that join "fixes" an as-is defect in passing, and the AD-7 or AD-13 behaviour changes without anyone deciding it. **Fix:** AD-21 says "append the note; do not change the existing join semantics", or schedule the fix deliberately.

---

## H6 — Title and axis ownership, and the grouping dimension lost by the convention · **Medium**

**Units:** chart_rules (title builder) vs text2sql prompt vs UI.

- AD-20 collapses every category grouping into one column name, `category`. FR-8 requires that "the other axis is labelled with the Category grouping" and that the title names what is measured. With `category` alone, chart_rules can only write "Spending by category" for a **merchant** comparison: compliant, but wrong.
- `period_start` and `period_end` are *optional*, yet FR-8 makes the period in the title mandatory. Dev A omits the period when they're absent. Dev B falls back to the first and last `period`, which doesn't exist on a bar chart. Neither AD says which.
- The title format ("October 2026" vs "2026-10-01 – 2026-10-31") has no owner. A UI dev may also prepend or rewrite the title, because AD-23 gives the UI "labels" ownership in its "Prevents" wording.
- The value-axis label (`USD` vs `Spending (USD)`) has no owner.

**Tighten:**
- **AD-20:** allow the label columns `category`, `subcategory` or `merchant` (each mapping to a fixed axis noun), or require a constant column `grouping ∈ {category, subcategory, merchant}`.
- **AD-20:** make `period_start` and `period_end` **required** on category comparisons, as constant ISO dates. Varying values across rows → `unsuitable_shape`.
- **AD-19:** chart_rules is the sole builder of `title`, `x_label` and `y_label`, from a fixed template table:
  - title: `"{Measure} by {grouping} — {period text}"`
  - y-axis: `"{Measure} (USD)"`
  - period text: one format per range shape.
- **AD-23:** the UI renders `title`, `x_label` and `y_label` verbatim.

---

## H7 — Evaluation harness vs app runtime: runs contaminate each other · **High** (new AD needed)

**Units:** eval runner story vs the app as built. Nothing in the spine binds the runner except "plain script, calls `/process` and `/process-stream`, cold then warm".

- **History seeding.** `_prepare_request` seeds every *new* session with the customer's last 5 messages from the last 24 h. Only `Cust_0410` has data, so question *n* is context-built with questions *n-1…n-5* and their FR-5 notes. The parity call to `/process-stream` sees the `/process` call's turn. The warm run sees the cold run. `context_builder` output differs, so `banksql_queries` differs, so the **SQL cache key differs** (it hashes `banksql_queries`). The "warm" run is then partly cold, with fresh SQL that may or may not follow the convention, and the SM run rule (same decision cold and warm) fails for reasons unrelated to charts.
- **Cache-key stability.** Even with clean history, the key depends on LLM wording (supervisor strip, context rewrite), so warm-run cache hits aren't guaranteed. AD-20's claim "because the convention lives in the SQL, cold and warm runs give the same Chart Decision" holds only on a cache hit.
- **Rate limit.** About 30 questions × 2 endpoints × 2 runs is 120 calls against `20/minute` per IP, so the runner gets 429s. One dev's runner treats them as failures; another retries silently.
- **SM-3 and SM-C2 need "feature off".** No switch exists. `CHART_FAULT_INJECTION` is not an off switch, and it is a global setting, so turning it on means restarting the app. `/update-settings` accepts only provider, temperature and max_tokens.
- **Relative dates.** The data ends 2026-06-13 and today is 2026-11-22. "Last month" returns zero rows, "this year" gives 6 monthly points, not the 11 in UJ-2, and labels decay as the calendar moves.
- **Cache clearing.** It is unspecified whether the runner deletes `query_cache` and `qcache:*` itself (opening `transactions.db` outside the app) or the operator does it by hand.

**New AD-25 (Evaluation harness contract):**
- Each request uses a fresh `session_id`, **and** the app runs with `HISTORY_LOOKBACK_HOURS=0` during evaluation.
- The runner throttles to ≤ 18 req/min and treats 429 as a retry, not a failure.
- Dataset questions use absolute periods ("in March 2026"), or the runbook requires `transaction_data_enhance.py shift` plus a DB rebuild first.
- Add `CHART_FEATURE_ENABLED` (default true). When false, `chart_builder` writes `decision="none"` with no reason and `response_generator` skips the note. Or explicitly drop SM-3 and SM-C2 from automation.
- Fault-injection rows run in a separate app start with `CHART_FAULT_INJECTION=true`.
- The runner records whether `text2sql_generator` hit the cache (add `details.cache_hit` to the text2sql trace entry), so a cold/warm mismatch caused by a key miss is reported separately from a chart-rule mismatch.
- The runner reads results only through the H1 contract.

---

## H8 — The prompt can silently top-N, which the chart rules can't see · **Medium**

**Units:** text2sql prompt story vs chart_rules story.

Today's examples use `LIMIT 20`, `LIMIT 1` and `ORDER BY … DESC LIMIT n` freely. A prompt dev adds the convention while the existing "top"/LIMIT habits stay. For "pie chart of spending by merchant" (14 merchants), the LLM writes `… GROUP BY merchant ORDER BY spending DESC LIMIT 8`. chart_rules, which may not read SQL (AD-20), sees 8 rows and draws a bar chart. That violates FR-1 (no top-N truncation) and UJ-4, exactly on the demo question. A `LIMIT 10` that stops at 10 still reports `too_many_categories`, so the result depends on which number the LLM picked.

**Tighten AD-20:** convention-shaped queries carry **no `LIMIT`** unless the user asked for "top N". In that case the label column is still `category`, and the Result is the top-N the user asked for. Add a counter-example to the prompt and a dataset row ("chart my spending by merchant", 14 merchants → `too_many_categories`).

---

## H9 — Envelope helper vs a failed `response_generator` · **Low–Medium**

**Units:** envelope helper story vs `/process-stream` as-is.

`chart_builder` runs before `response_generator`. If `response_generator` then fails, `/process` returns 500 and `/process-stream` substitutes `TECHNICAL_FALLBACK_MESSAGE`. But the AD-22 helper builds `visualization` from `chart_response` with no condition, so the stream shows a chart under "I'm unable to process your request". That contradicts FR-13's spirit, and it widens the AD-13 divergence the spec excludes.

**Tighten AD-22:** the helper returns `null` unless `response_generation_response` is present and `fallback_response.fallback_node != "response_generator"`.

---

## H10 — `ChartResponse` invariants and the "never raise" handler · **Low**

- AD-19 states invariants (`skip_reason` iff `none`, `spec` iff not `none`) but not *where they are enforced*. If enforcement is a `model_validator`, a bug in the happy path raises inside the `try`, which correctly becomes `chart_error`. But if `chart_builder`'s `except` constructs `ChartResponse` with a field the validator rejects (for example `explicit_request` left unset), the exception escapes the handler and the graph 500s.
- `decision` and `spec.chart_type` duplicate the same fact. The trace uses one and the UI uses the other.

**Tighten AD-18 and AD-19:**
- The except path assigns a module-level constant, `CHART_ERROR_RESPONSE = ChartResponse(decision="none", skip_reason="chart_error", explicit_request=False, spec=None)`, built and validated at import time.
- A validator enforces `decision == spec.chart_type`.
- Where the regex runs (node vs `chart_rules`) is decided: in `chart_rules`, per AD-17's "node only reads state, calls rules".

---

## H11 — UI "spec is valid" check is undefined · **Low**

AD-23 says the UI renders nothing if "the spec is valid" fails, but doesn't define valid. UI dev A re-implements the 3–8 and 3–12 limits, so the rules gain a second owner and a later limit change breaks charts silently. UI dev B checks nothing beyond `Array.isArray`. In addition, the current gate `should_visualize && plotly_json` must be replaced in the *same* change as the backend envelope change, or charts silently never appear.

**Tighten AD-23:**
- "Valid" means **structural only**: `chart_type ∈ {bar,line}`, a non-empty `points`, each `label` a string and each `value` a finite number, and string titles and labels.
- The UI never re-applies eligibility limits.
- AD-22 and AD-23 ship together. The old gate is removed in the same commit.

---

## H12 — Balance tie-break holds only by data coincidence · **Low**

The rule `(transaction_date, transaction_id) DESC` gives the true end-of-day balance today only because the data pipeline happens to order same-day rows by `transaction_id`. The IDs are random hex (`TX_5ac7b0d9`). Running-balance continuity was verified 3950/3950 under both `rowid` and `transaction_id` order. If the pipeline is regenerated with new IDs, the "last" row is arbitrary but still deterministic, and the plotted balance stops matching the real closing balance.

**Tighten AD-20:** note this dependency, and require `transaction_data_enhance.py` to keep the same-day order sorted by `transaction_id`, or switch the tie-break to `rowid` (insertion order from the xlsx, which is the running-balance order).

---

## Smaller notes (no new AD needed, but write them down)

- **AD-6 extras for `chart_builder`.** How the fault-injection flag reaches the node is unspecified. `Agent_Graph` holds no `AppSettings`, so one dev calls `AppSettings()` inside the stage and another threads it through the wrapper. State: "passed by the wrapper as an extra; the stage never instantiates settings."
- **AD-12 "silent" registration.** `_STREAM_STEP_LABELS["chart_builder"] = None` works with the current `if label:` check. Write down that the stream loop must keep treating `None` as silent rather than as a missing key, so nobody later "fixes" it to `KeyError`.
- **Bar order.** "Points are already in plot order" gives the line order (time) but no bar order. Pick one, for example SQL order as returned or value-descending, and forbid the UI from re-sorting.
- **Greeting and guardrail paths.** "Hi! can you chart my spending?" can trip the `greeting` fallback, which gives skip reason `fallback` plus a note appended to a greeting. The behaviour follows the spec, but add a dataset row so it's a decision and not a surprise.
- **The value column is trusted.** chart_rules trusts that `spending` excludes income (FR-8 "mixed directions" is not detectable from rows). AD-20 should say this outright: the prompt owns direction filtering, and a misnamed column is a prompt defect, not a chart-rules defect.

## Summary of tightenings

| Hole | Severity | Close with |
| --- | --- | --- |
| H1 trace entry and runner read path | Critical | AD-22: fixed `chart_builder` entry schema, including `note_appended`; runner reads only that and `visualization.chart_type` |
| H2 `period` production, sort and display | Critical | AD-20: `strftime`-only periods, a regex per grain, invalid → `unsuitable_shape`. AD-19: display-label table owned by chart_rules. AD-23: category x-axis |
| H3 classification gaps vs FR-3 | High | AD-20: an explicit ordered decision table (empty/None, raw, scalar → `too_few_points`, malformed convention → `unsuitable_shape`, capped convention → `too_many_*`); amend the PRD "truncated" glossary |
| H4 explicit-request regex | High | AD-21: pinned `EXPLICIT_CHART_PATTERN` plus a shared positive/negative case table; decide bare "pie" and negation |
| H5 note ownership and placement | High | `CHART_NOTES` in `chart_rules`; note appended outside all branches; response-generator prompt line forbidding chart talk; AD-8 wording updated |
| H6 title and grouping | Medium | Grouping-aware label column; required `period_start`/`period_end`; chart_rules sole title and axis builder |
| H7 eval harness | High | New AD-25: no history seeding, throttling, absolute dates, a feature-off flag, cache-hit visibility |
| H8 silent top-N | Medium | AD-20: no `LIMIT` on convention queries unless the user asked for "top N" |
| H9 chart under technical error | Low–Med | AD-22 helper returns `null` if the response generator failed |
| H10 handler robustness | Low | A constant `CHART_ERROR_RESPONSE`; `decision == spec.chart_type` validator |
| H11 UI validity | Low | Structural validity only; ship together with the envelope change |
| H12 balance tie-break | Low | Document the dependency, or use `rowid` |
