---
reviews: ARCHITECTURE-SPINE.md (FinTell baseline + Transaction Charts, altitude: feature, status: draft)
focus: AD-17..AD-24 and the chart-feature amendments to AD-3/5/6/7/10/12/13/15/16
method: good-spine rubric; every claim checked against src/, prompts/, requirements.txt, AGENTS.md and the Transaction Charts PRD
date: 2026-11-22
---

# Spine review: Transaction Charts (rubric walk)

## Verdict

The chart amendments fit the brownfield code closely. The new divergence points are mostly fixed: one code-only node after `join`, the optional-stage rule, a typed output, shared envelope building, and the cold/warm cache reasoning. Most of the as-is claims I checked hold, including the AD-10 correction, the `final` event replacing the streamed text, and the registration points in `app.py`. Before epics are cut, the spine needs one revision for four real gaps:

1. The SQL-correction path is not bound by the result-shape contract.
2. The theme behaviour it prescribes doesn't exist in the UI.
3. The trace entry's shape is left to its two consumers (UI and eval runner).
4. The column convention cannot express what FR-8 and the FR-5 note need to say.

## Rubric scorecard

| Check | Result | Notes |
| --- | --- | --- |
| Fixes the real divergence points for the chart epics, misses none | Partial | Missing: `sql_correction` as a party to AD-20 (F1); the trace-entry schema and the `buildDetails` UI registration point (F3); how `CHART_FAULT_INJECTION` reaches the node (F6); the "feature off" baseline for SM-3 (F5). |
| Every Rule enforceable and prevents its divergence | Partial | AD-20's "one row per transaction" can't be tested from columns alone (F4). AD-21's "standalone" keyword match is underspecified (F7). AD-23's theme redraw has no theme to redraw (F2). The rest are concrete and checkable. |
| Nothing in Deferred lets units diverge | Pass | The Deferred rows are all safe for this feature. The `fallback_node` overwrite is harmless because the chart rules key on presence. The node registry is deferred with a trigger. |
| Named tech verified-current | Pass (one wording nit) | The pins match `requirements.txt`. Plotly.js 2.35.2 matches `index.html:10`. "Current upstream major is 4.x" is confirmed: 4.0.0 was released 2026-08-24, and 4.3.0 exists. The "Python `plotly` removed" row states as fact something not yet done: `requirements.txt:111` still lists it (F9). |
| Ratifies, doesn't contradict, the brownfield code | Mostly pass | One contradiction (theme, F2), one incomplete vocabulary (`fallback_reason`, F8) and one omitted AD-10 nuance (F9). The other claims were verified (see below). |
| Covers the driving spec's capabilities | Partial | FR-1..FR-7, FR-10..FR-15 and SM-1/2/4/5/6 are covered. FR-8's grouping-specific axis/title/note and FR-9 theme are not really covered (F2, F4). SM-3 needs an on/off switch that is not decided (F5). |
| Altitude dimensions decided / deferred / open | Pass | The ops envelope (AD-14, AD-16, the deployment diagram, the Deferred scaling row) is covered. The CDN dependency is covered by AD-15/AD-23 plus Deferred. Rate limiting and metrics are settled for a feature that adds no endpoint. |
| Bloat / clarity | Minor | See "Bloat and wording" below. |

## Claims verified against the code (hold)

- **AD-10 as-is correction:** the regex `\b(count|sum|avg|min|max|group_concat)\b\s*\(`, the `LIMIT 20` / `ORDER BY transaction_date DESC` rewrite and the clamp (`sql_execution.py:23-43`), and the Python hard cap with the notice on any result over 20 rows (`sql_execution.py:60-63`). All correct.
- **Final SSE event replaces streamed text:** `index.html:1930-1949` sets `assistantBubble.innerHTML` from `payload.response`. So the AD-21 note, which is never streamed, does appear. Correct.
- **Registration points:** `_STREAM_STEP_LABELS` (`app.py:510-519`; a missing key and a `None` value are both silent because of `if label:` at :557), `_build_agent_trace` (`app.py:159`), the token allowlist (`app.py:563`), `TRACE_ICONS` (`index.html:2244`). All correct, but the list is incomplete (F3).
- **AD-3 join order** (fallback, then help, then SQL rephrase) matches `response_generator.py:18-67`.
- **AD-7 as-is:** `_supervisor_router` → `END` (`agent_graph.py:152-153`). A second `query_error` → `end` (`agent_subraph.py:125-130`). `help_center` → `join` directly (`agent_graph.py:83`). `needs_separate_questions` with `fallback_message=None` (`text2sql_generator.py:83-88`). All correct.
- `sql_correction` clears `fallback_response` on success (`sql_correction.py:64`). So a corrected query can still chart, and the chart rules' `fallback` check is sound.
- **AD-12 aliases table:** supervisor `fallback_node="supervisor"` (`supervisor.py:89`), the inner `sql_generation` node (`agent_subraph.py:31`), and `fallback_agent` overwriting `fallback_node` (`fallback.py:96,106`). All correct.
- **AD-13 as-is:** `/process` raises at `app.py:453` and returns a 500; `/process-stream` substitutes `TECHNICAL_FALLBACK_MESSAGE` and persists it (`app.py:569-581`). Correct.
- **AD-2/AD-20 cache key:** it hashes `str(banksql_queries)` (`text2sql_generator.py:28-35`), so the supervisor stripping presentation words (AD-21) also improves cache reuse. Correct.
- **The visualization leftovers listed for removal** all exist: `app_models.py:56-66,114`, `app.py:279-299,480-489,516,586-594`, `index.html:1957-2011,2251,2337-2348`.

## Findings

### F1. [HIGH] `sql_correction` is outside the AD-20 result-shape contract, so corrected queries break the convention and cold/warm parity

**Evidence**
- AD-20 binds `prompts/text2sql_generator.yml`, `chart_rules.py` and the SQL cache. When execution fails, `sql_correction` rewrites the SQL (`sql_correction.py:57`) using `prompts/sql_correction.yml`. That prompt doesn't know the column convention, and its schema is stale. It lists `country`, `card_type`, `card_present` and `channel`, and leaves out `transaction_type` and `balance` (`sql_correction.yml:9-24`).
- FR-15 balance-trend SQL (CTEs or window functions, last row per period) is the most likely to need correction. The corrected SQL can then drop the `period`/`balance` aliases or invent columns, and the result becomes `unsuitable_shape`.
- AD-2 as-is: the *pre-correction* SQL is cached. A warm run replays the failing SQL and calls the correction LLM again, so the warm Chart Decision is only as stable as an LLM rewrite. The AD-20 statement "cold and warm runs give the same Chart Decision" doesn't hold on the correction path.
- AD-9's schema-change rule names only the supervisor and text2sql prompts. It leaves out `sql_correction.yml`, which has already drifted.

**Fix**
- Add `prompts/sql_correction.yml` to the AD-20 Binds. The rule: "the correction prompt preserves output column aliases verbatim and carries the same convention block". Then fix its schema.
- Add `sql_correction.yml` to the AD-9 schema-change rule and to AGENTS.md's cross-component rule.
- Either qualify the AD-20 cold/warm claim with "for SQL that executes without correction", or have the eval runner label the questions that needed correction (`sql_query_correction` is already in the trace).

### F2. [HIGH] FR-9 and AD-23 prescribe theme-following and redraw-on-theme-change, but the UI has no theme switch

**Evidence**
- `index.html` has one `:root` token set (`index.html:13-30`).
- It has no `prefers-color-scheme`, no `data-theme` and no theme toggle. A grep for `theme` and `dark` finds nothing in `index.html`.
- AD-23 ("redraws existing Charts when the theme changes") and the PRD test ("Switching theme leaves the Chart readable") describe a capability that doesn't exist.

**Why it matters:** the story writer either invents a dark theme (scope creep across the whole UI) or writes an untestable acceptance criterion. Two stories could resolve it differently.

**Fix:** choose one and say so in AD-23.
- (a) "The UI has one theme today. Charts take colours from the existing `:root` tokens. Redraw-on-change applies only if a theme switch is added." Raise this as a PRD correction to FR-9.
- (b) Make adding a dark theme an explicit, separately scoped item.

Add an Open Question until the owner decides.

### F3. [MEDIUM] The `chart_builder` trace entry has two consumers but no fixed shape, and AD-12 misses a UI registration point

**Evidence**
- FR-14 requires the decision to appear in the "existing UI trace panel". `buildDetails` in `index.html:2285-2360` branches per `step.agent` (for example `visualization_agent` at :2337). Without a `chart_builder` branch, the panel shows only a title and summary.
- AD-12's registration list names only the `TRACE_ICONS` map from `index.html`.
- AD-22 says the entry is "showing the decision and the skip reason" but doesn't name the keys. The eval runner (SM-4, SM-6) and the UI both parse this entry, and so would any parity check.

**Fix**
- In AD-22, fix the entry: `agent="chart_builder"`, `status ∈ {ok, skipped, error}`, `details = {decision, skip_reason, explicit_request}`, with values exactly as in `ChartResponse`.
- Add "the `buildDetails` branch in `index.html`, if the node has details" to the AD-12 registration list.
- State that the runner reads the decision from this entry (`/process` returns no other field for it).

### F4. [MEDIUM] The AD-20 convention can't express the grouping, so FR-8 axis labels and the FR-5 note can't say "merchants" vs "categories"

**Evidence**
- AD-20 forces every comparison into one label column, `category`. FR-8 requires "The other axis is labelled with the Category grouping". FR-5 and UJ-4 use the example note "There are too many merchants to chart clearly".
- `chart_rules` can't tell `merchant_brand_name` from `merchant_system_category1_name` or `category2_name` without reading SQL, and AD-20 forbids reading SQL. So `x_label`, the title ("Spending by category — …") and the per-`SkipReason` note can only be generic.
- In the same AD, "`raw_list` if it has `transaction_id` or one row per transaction" can't be tested. Nothing in the columns shows "one row per transaction" when `transaction_id` is absent (for example `SELECT transaction_date, amount_usd …`).

**Fix**
- Widen the label column to a small closed set: `merchant` | `category` | `subcategory` | `period` (or keep `category` and add a constant `grouping` column). Mirror it as constants in `chart_rules.py`, which derives `x_label`, the title and the note noun from it.
- Replace "one row per transaction" with a column test, for example "any of `transaction_id`, `transaction_date`, `merchant_brand_name` present alongside `amount_usd` without a convention value column".
- The existing text2sql example at `text2sql_generator.yml:171` (`GROUP BY year, month … ORDER BY year DESC, month DESC LIMIT 20`) contradicts the convention. Name it as an example that must be rewritten.

### F5. [MEDIUM] SM-3 needs a "feature off" run, but the spine has no switch and no baseline

**Evidence:** SM-3 compares median latency "with the feature on versus off". The spine has `CHART_FAULT_INJECTION` but no enable flag, and doesn't say whether "off" means a feature flag or the pre-feature commit (`936d6a5`/`cc00609`). The prompt changes (AD-20, AD-21) also change Text Reply latency, so the two choices measure different things.

**Fix:** decide one of these.
- (a) `AppSettings.CHARTS_ENABLED` (default true). When false, `chart_builder` writes nothing and the envelope gets `visualization: null`. This measures node cost only.
- (b) "Off = commit `936d6a5` against the same DB." This measures the whole feature, prompts included.

Record the choice next to the evaluation convention.

### F6. [LOW-MEDIUM] How `CHART_FAULT_INJECTION` reaches `chart_builder` is not decided

**Evidence:** `Agent_Graph` doesn't receive `AppSettings` (`agent_graph.py:16-22`; `_prepare_request` at `app.py:426-430`). Builders would either pass it as a code-only extra or have the node build `AppSettings()` itself, which re-reads `.env` on every request and bypasses `app.state`. AD-18's "no other code reads that flag" doesn't settle who passes it.

**Fix:** "`_prepare_request` passes `app_settings.CHART_FAULT_INJECTION` (and `CHARTS_ENABLED`, if adopted) into `Agent_Graph`, which hands it to `chart_builder` as an extra. Stages never instantiate `AppSettings`." Also add `CHART_FAULT_INJECTION` to `AppSettings` (it doesn't exist yet) in the seed list, which already names `app_setting.py`.

### F7. [LOW-MEDIUM] The AD-21 explicit-request matcher is underspecified and narrows the PRD silently

**Evidence**
- "A standalone 'chart', 'graph', 'plot' … or a named chart type followed by 'chart' or 'graph'". The second clause adds nothing, because "pie chart" already contains the standalone word "chart".
- "Standalone" doesn't say whether "charts", "graphs", "plotted" or "visualisation" match.
- The PRD glossary counts a bare named chart type ("bar", "line", "pie"…) as explicit. The spine drops that, probably wisely ("line items"), but doesn't say so.
- SM-1 is 100%, so the eval dataset labels and the matcher have to agree exactly.

**Fix:** define the matcher as one regex constant in `chart_rules.py` (for example `\b(chart|graph|plot|visuali[sz]e|visuali[sz]ation)s?\b`, case-insensitive), state that a bare type name doesn't count, and require the UJ-4 dataset rows to be labelled against that constant.

### F8. [LOW] The `fallback_reason` vocabulary in the Errors convention is incomplete

**Evidence:** `fallback_agent` writes `mixed_valid_invalid`, `multiple_valid` and `multiple_invalid` (`fallback.py:74,81,88`). The guardrail writes `SYSTEM_VIOLATION`, `SQL_VIOLATION` and `PII_VIOLATION` (uppercase, `guardrails.py:118`). The convention lists only `exception`, `query_error`, `needs_separate_questions`, `greeting` and "the guardrail violation labels".

**Fix:** list the closed set verbatim. The chart feature doesn't branch on these values, so this is accuracy only.

### F9. [LOW] Small as-is and Stack inaccuracies

- AD-10: the SQL rewrite runs only when the statement starts with `select` (`sql_execution.py:23`). A `WITH …` CTE, which AD-11 and AD-20 invite for the balance trend, skips the rewrite and the clamp, and only the Python cap applies. Add this to the as-is bullet.
- Stack: "Python `plotly` — removed from requirements.txt" is still present at `requirements.txt:111`. Word it as "to be removed (seed)".
- AD-20: SQLite has no ISO-week `strftime`. `%W` is a Monday-based week 00–53. Either define `YYYY-Www` as `strftime('%Y-W%W')` or drop weekly periods.
- AD-20/AD-17: say what `database_query_results is None` with BankSQL present means. This happens for an empty `sql_query`, which sets `sql_query_correction=True` and returns with no fallback (`sql_execution.py:45-48`), and for a non-SELECT statement. It should presumably be `empty_result`.

## Lower-priority notes

- AD-21: with an explicit request, the note is also appended after guardrail and other fallback messages (skip `fallback`) and after help-only replies (`no_transaction_data`). An example: "plot my card 4111…" gets a PII alert followed by a "no chart" line. That follows FR-5 literally, but the spine should either confirm it or exclude `fallback` from the note. Each fixed note string should also say what it says for `fallback`.
- AD-23 / FR-13: fault injection covers the backend only. Say how the UI paths (malformed spec, Plotly missing) are checked: a manual check, or a spec-validation function that a crafted spec can exercise.
- AD-19: `ChartSpec` has no currency or unit field, while FR-8 requires USD. That is fine if `y_label` always carries "USD". Say that `chart_rules` sets it.

## Bloat and wording

- The **Structural Seed "Files the chart feature touches"** list and the **Runbook** are useful, but they are seed and story detail, not invariants. Keep them, marked as seed. Don't add more.
- **AD-12's** "always present in the trace when `chart_response` is set" restates FR-14. "Every path through `join`" is the actual invariant and is clearer.
- **AD-10's "Chart feature" line** is redundant with AD-20's classification bullet. A capped result always has more than 20 points, which already exceeds both limits. Keep one.
- **AD-24's** "Code-only stages … write no metrics entry" is clear. "Adding a reducer stays deferred" duplicates the Deferred row and can go.
- **AD-5's** last bullet ("Who owns the branch order … stays open") duplicates Deferred.
- Several ADs mix normative Rule text with explanation ("Because the convention lives in the SQL…"). Put rationale under **Prevents**, so the Rule list reads as checkable statements.

## What the chart amendments get right

- Choosing a code-only node on a straight `join → chart_builder → response_generator` edge removes the TTFT, determinism and "second writer" risks in one move, and it fits the paradigm.
- The required/optional stage split (AD-18) resolves the PRD's open question on who writes `fallback_response`.
- `SkipReason` is a single `Literal` with FR-3 order, and the `ChartResponse` invariants (spec exactly when decision ≠ none) are enforceable with Pydantic validators.
- Keeping the chart in the `final` event only, adding no new SSE type, and sending data-only `ChartSpec` (no Plotly JSON) are well matched to the existing UI reconcile path.
- The AD-10 as-is correction is accurate and was worth making.

Sources (Plotly.js version check): [plotly.js 4.0.0 CHANGELOG on unpkg](https://app.unpkg.com/plotly.js@4.0.0/files/CHANGELOG.md), [plotly.js 4.3.0 RELEASE.md on unpkg](https://unpkg.com/plotly.js@4.3.0/RELEASE.md)
