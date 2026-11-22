---
status: final
stepsCompleted: [1, 2, 3, 4]
inputDocuments:
  - _bmad-output/specs/spec-transaction-charts/SPEC.md
  - _bmad-output/planning-artifacts/architecture/architecture-Agentic_BChatbot-2026-11-22/ARCHITECTURE-SPINE.md
  - _bmad-output/specs/spec-transaction-charts/glossary.md
  - _bmad-output/specs/spec-transaction-charts/evaluation-plan.md
  - _bmad-output/planning-artifacts/prds/prd-Agentic_BChatbot-2026-11-22/prd.md (source only, rationale)
  - _bmad-output/planning-artifacts/prds/prd-Agentic_BChatbot-2026-11-22/addendum.md (source only, rationale)
---

# Agentic_BChatbot (FinTell) — Transaction Charts — Epic Breakdown

## Overview

This document breaks the FinTell Transaction Charts feature into epics and stories. Requirements come from `SPEC.md` (CAP-1..CAP-12) and its binding companions: the architecture spine (AD-1..AD-25, IDs stable), the glossary (terms used exactly) and the evaluation plan. The PRD and addendum are rationale only; where they differ, the spec and spine govern. No UX design contract exists; UI requirements come from CAP-7, CAP-9, CAP-10 and AD-23.

## Requirements Inventory

### Functional Requirements

Functional requirements keep their SPEC IDs (CAP-n). PRD FR numbers are shown for traceability.

- **CAP-1: Bar Chart for Category comparisons** (FR-1). A Result comparing an amount across 3–8 Categories gets a Bar Chart under the Text Reply. 3 or 8 Categories → Bar Chart; 2 or 9 → none. Above 8: no top-N, no "Other" bar, no regrouping.
- **CAP-2: Line Chart for Time Point trends** (FR-2). A Result across 3–12 Time Points gets a Line Chart. 12 monthly Time Points → Line Chart; 13 → none. Daily rows across a whole month → none, not regrouped into weeks. Points plot in time order whatever the row order; only Time Points present are plotted (no gap filling).
- **CAP-3: Balance trend** (FR-15). "Show my balance trend this year" returns the end-of-period balance per Time Point (the `balance` of the period's last transaction, tie-break by `(transaction_date, transaction_id)` descending), charted as a Line Chart. With 11 months of data: 11 end-of-month balances. Never one row per transaction. More than 12 Time Points → no Chart. Two customers asking get only their own series.
- **CAP-4: Holding back** (FR-3, FR-6, FR-7, FR-8). Each "no chart" Chart Decision carries exactly one Skip Reason; each of the nine Skip Reasons gives no Chart; first matching row of the AD-20 decision table wins (e.g. empty Result after fallback → `fallback`). Grouped-two-ways, mixed spending/income (not net or balance), and combined multi-SQL-Sub-query Results that don't fit one shape → `unsuitable_shape`. Help-centre content is never charted. A non-raw 1–2-row Result ("current balance", "income vs spending") → `too_few_points`.
- **CAP-5: Bar and Line only** (FR-4). No pie, scatter, area or other type is ever produced. "Pie chart of spending by category" with 5 Categories → Bar Chart, with no mention of the substitution in the Text Reply.
- **CAP-6: Explicit Chart Request** (FR-5). Chart wording on a transaction question is answered as a normal transaction question, never sent to fallback as off-topic ("Chart my spending by merchant", 14 merchants → answered, no Chart). With an Explicit Chart Request and "no chart", the Text Reply ends with the fixed FR-5 note for that Skip Reason; left out for `chart_error`. Without an Explicit Chart Request, "no chart" adds nothing. The note is saved with chat history as part of the Text Reply.
- **CAP-7: Chart content and placement** (FR-8, FR-9). Title names measure and period (e.g. "Spending by category — October 2026"). Value axis labelled in USD; other axis names the Category grouping or Time Point period. Single direction (spending or income) except net and balance, which may go negative and plot as-is. Renders directly under its Text Reply, in the UI's existing colour tokens, fits the chat column at 1280 px without horizontal scrolling.
- **CAP-8: Streaming and endpoint parity** (FR-10, FR-11). On `/process-stream` the Text Reply streams as today; the Chart arrives only in the `final` event. Median time to first token changes by ≤ 200 ms. No Chart Path output streamed as tokens; no new event type. Every evaluation question gives the same Chart Decision on `/process` and `/process-stream`, with the same empty `visualization` value when there is no Chart.
- **CAP-9: Charts not saved** (FR-12). After a page reload, earlier Replies show no Chart, no error and no placeholder.
- **CAP-10: Fail-safe Chart Path** (FR-13). Server: an injected Chart Path error gives the normal Text Reply, no Chart, Skip Reason `chart_error`, no fallback routing or fallback text. Browser: malformed or partial chart data, or a chart library that fails to load, shows the Text Reply with no Chart and no page-breaking error.
- **CAP-11: Traceable Chart Decision** (FR-14). Every Reply's `agent_trace`, and the UI trace panel, show the Chart Decision (chart type, or "no chart" with Skip Reason). The Chart Decision is not saved anywhere. A supervisor error ending with no Reply (AD-7 as-is) is exempt from CAP-4 and CAP-11.
- **CAP-12: Measurable correctness** (PRD §7). A run of ~30 labelled questions in `tests/data/`, using the evaluation-plan passes, reports SM-1..SM-6 and the counter-metrics. Cold and warm passes give the same Chart Decision, excluding rows that went through `sql_correction`.

### NonFunctional Requirements

- **NFR-1 (latency, SM-3 / CAP-8):** ≤ 2 s median added end-to-end for charted Replies; ≤ 200 ms change for un-charted Replies and for time to first token.
- **NFR-2 (determinism, AD-17):** The Chart Decision is code-only — no LLM, SQL or network call — and a deterministic function of state, made before `response_generator` runs.
- **NFR-3 (Text Reply primacy):** The Text Reply always comes first and is never replaced, delayed or broken by a Chart. It changes in only two ways: the FR-5 note (CAP-6) and the end-of-period balance answer (CAP-3).
- **NFR-4 (fail-safety, AD-18 / SM-5):** The chart stage is optional; its failure never writes `fallback_response`. Zero lost or changed Text Replies under fault injection.
- **NFR-5 (correctness targets):** SM-1 = 100%, SM-2 ≥ 90%, SM-4 = 100%, SM-6 ≥ 90%. Counter-metrics SM-C1 (chart rate) and SM-C2 (Text Reply drift) reported, not optimised.
- **NFR-6 (reads only the Result, AD-11/AD-20):** The Chart Path uses only `database_query_results`, `input_request.user_query`, `banksql_queries` and `fallback_response`; no extra queries, gap filling, regrouping or top-N.
- **NFR-7 (customer scoping, AD-11):** Every new SQL guidance line or example applies `customer_id = '<customer_id>'` in every subquery and CTE.
- **NFR-8 (no interface growth, AD-13/AD-22):** No new endpoint, envelope key or SSE event type. Envelope stays `{response, visualization, agent_trace}`; `visualization` is a `ChartSpec` or `null`.
- **NFR-9 (stakes, AD-14):** Internal demo, single local instance, seeded data; NFRs sized to that. No chart accessibility requirement beyond the Text Reply carrying the full answer.
- **NFR-10 (browser compatibility, AD-15/AD-23):** Desktop browser at 1280 px; Plotly.js 2.35.2 from CDN loaded with `defer`, treated as an optional service. Phone width and dark theme out of scope.

### Additional Requirements

From the architecture spine. No starter template applies (brownfield; existing FastAPI + LangGraph app).

**Models and state (AD-4, AD-19)**
- Add to `src/app_models.py`: `SkipReason` Literal with the nine labels; `ChartPoint(label: str, value: float)`; `ChartSpec(chart_type: bar|line, title, x_label, y_label, points)`; `ChartResponse(decision: bar|line|none, skip_reason, explicit_request, spec)` with `skip_reason` set exactly when `decision == "none"` and `spec` set exactly when it isn't.
- `Graph_State.chart_response: ChartResponse | None = None`, written only by `chart_builder`; **not** in `SubGraph_State`.
- Remove `VisualizationDecision`, `VisualizationResponse`, `Graph_State.visualization_response`.

**Chart rules (AD-17, AD-19, AD-20, AD-21)** — `src/utils/chart_rules.py`, stdlib only (`ast`, `re`, `datetime`), pure functions and constants, single owner of:
- the AD-20 decision table (10 rows, first match wins), parsing rows with `ast.literal_eval`, setting aside and recording the AD-10 notice string as `truncated`;
- grain patterns for `period` (`%Y`, `%Y-%m`, `%G-W%V`, `%Y-%m-%d`), sorting on raw ISO period before formatting display labels;
- title, axis and point-label tables keyed by AD-20 column names (value column → measure; label column → grouping; grain → label format; period phrase from `period_start`/`period_end` for bar, first/last period for line; bar without them → no period phrase);
- `EXPLICIT_CHART_PATTERN` (case-insensitive, whole words; chart/graph/plot/visualise/visualize word forms; chart types followed by chart/graph/plot or preceded by "as a"/"as an"; no match inside `Chartered`/`online`; negations not explicit; never extracts the requested type);
- `CHART_NOTES` (one fixed plain string per `SkipReason`) and `note_due(chart_response)`.
- Values finite, USD, rounded to 2 decimals.

**Chart node (AD-5, AD-6, AD-12, AD-17, AD-18, AD-24)** — `src/agents/chart_builder.py`
- Code-only signature `(state, logger, *extras)`; reads state, calls `chart_rules`, writes `chart_response`; no metrics entry.
- Plain edge `join → chart_builder → response_generator` in `src/agent_graph.py`; no new branch, no router, no supervisor label.
- Whole body in `try/except Exception`; on failure, a `ChartResponse` from literal constants (`decision="none"`, `skip_reason="chart_error"`, `explicit_request=False`, `spec=None`), logs the exception, never writes `fallback_response`.
- `CHART_FEATURE_ENABLED` (default true) and `CHART_FAULT_INJECTION` (default false) in `src/app_setting.py`, passed from `_prepare_request` through `Agent_Graph`. Disabled → `chart_response` stays `None` (no Chart, note or trace entry). Fault injection → raise inside the node's own `try`.
- One id `chart_builder` for node name, trace `agent`, UI icon key. Silent in `_STREAM_STEP_LABELS` (`None`), not on the token allowlist. All `visualization_agent` entries removed (no aliases).

**Reply assembly (AD-3, AD-21)** — `src/agents/response_generator.py`
- Append `CHART_NOTES[skip_reason]` as the last step, outside every branch, when `note_due` is true; no LLM call; covers help-only and fallback paths. Note reaches the UI via `final`, not streamed as tokens.

**Shell (AD-13, AD-22)** — `src/app.py`
- One shared helper builds `visualization`: `ChartSpec.model_dump()` when `decision ∈ {bar, line}` and `response_generation_response` exists; otherwise `null`. Both endpoints call it.
- `_build_agent_trace` adds a `chart_builder` entry whenever `chart_response` is set, just before `response_generator`: `title: "Chart"`, `status` ok/skipped/error, one-line `summary`, `details: {decision, skip_reason, explicit_request, note_appended, point_count, title}`, `metrics: null`.
- Chart and Chart Decision never persisted (Redis history, `chat_messages`, `request_metrics`). Chart only in the SSE `final` event.

**SQL execution row cap (AD-10)** — `src/agents/sql_execution.py`
- The `LIMIT 20` rewrite also treats `OVER (` and `GROUP BY` as aggregates, so window-function balance queries and grouped series aren't silently cut to 20 without the notice.

**Prompts (AD-8, AD-11, AD-20, AD-21)** — no new prompt file
- `prompts/text2sql_generator.yml`: AD-20 column convention (one label column `merchant`/`category`/`subcategory`/`period`; one value column `spending`/`income`/`net`/`balance`; optional `period_start`/`period_end`; `period` via `strftime` only; no `LIMIT`/top-N on comparisons and trends; list answers never use convention names) and FR-15 balance guidance (last row per period by `(transaction_date, transaction_id)` desc, customer filter in every subquery/CTE, `ORDER BY period`).
- `prompts/sql_correction.yml`: fix stale schema (drop `country`/`card_type`, add `transaction_type`, `balance`); keep the output column names of the SQL it repairs.
- `prompts/suprivisor_prompt.yml`: classify chart wording on a transaction question as `BankSQL` and strip presentation words from `sub_query` (filename misspelling is load-bearing).
- `prompts/response_generator_prompt.yml`: never mention charts.
- The AD-20 convention changes together in text2sql prompt, sql_correction prompt and `chart_rules` constants. Afterwards: `DELETE FROM query_cache` and delete Redis `qcache:*` keys.

**UI (AD-12, AD-15, AD-23)** — `src/index.html`
- Map `ChartSpec` to a Plotly bar or line trace; colours from existing `:root` CSS tokens read at draw time; x-axis `type: "category"`; titles in `{text: …}` form; no backend styling.
- Plotly 2.35.2 script with `defer`. Before drawing, check `window.Plotly` exists and the spec is valid (`chart_type` bar/line, ≥ 3 points, string labels, finite numbers); otherwise render nothing (no placeholder, no thrown error). Remove "Chart could not be rendered." placeholder and the `plotly_json` render path. Text Reply rendered first.
- Trace panel: `chart_builder` icon and a per-agent branch in `buildDetails`; remove `visualization_agent` icon and block.
- Reloaded conversations show Text Replies only.

**Dependencies (spine Stack)**
- Remove the unused Python `plotly` package from `requirements.txt`.

**Data and evaluation (AD-9, AD-25, Consistency Conventions)**
- `transactions` schema and data unchanged. Before evaluation/demo: `python data/data_processing/transaction_data_enhance.py shift`, then rebuild `transactions.db` (seeded data ends 2026-06-13). Rebuild wipes `chat_*`, `request_metrics`, `query_cache`. FR-15 tie-break relies on builder row order (verified 3950/3950).
- Dataset in `tests/data/` (data only, no pytest files), columns `question`, `customer_id`, `expected_chart`, `expected_skip_reason`, `explicit_request`, `expect_note`; ~30 rows covering the evaluation-plan "Must cover" list and all reference scenarios.
- Runner `scripts/eval_charts.py` (outside `tests/data/`), plain script, never imports app code; fresh `session_id` per request; app started with `HISTORY_LOOKBACK_HOURS=0`; ≤ 18 requests/min, retries HTTP 429 without counting; reads only the `chart_builder` trace entry and `visualization.chart_type`; five passes (cold after cache clear, warm, `/process-stream` parity, `CHART_FAULT_INJECTION=true`, `CHART_FEATURE_ENABLED=false` baseline); cold/warm check skips rows with a `sql_correction` trace entry.

**Verification convention (AGENTS.md)**
- No test suite. Verify by starting `python src/app.py` from repo root and calling `/process` (and `/process-stream` where relevant).

### UX Design Requirements

No UX design contract (DESIGN.md / EXPERIENCE.md) exists for this feature. UI behaviour is specified by CAP-7, CAP-9, CAP-10 and AD-23, captured above under Additional Requirements → UI. Open question PRD OQ-3 (draw missing periods as zero?) is owned by UX; the default in force is omit, no gap filling (CAP-2).

### FR Coverage Map

| CAP | Epic | Brief |
| --- | --- | --- |
| CAP-1 | Epic 1 | Bar Chart, 3–8 Categories |
| CAP-2 | Epic 1 | Line Chart, 3–12 Time Points, time order, no gap fill |
| CAP-3 | Epic 1 | End-of-period balance trend (FR-15 SQL guidance) |
| CAP-4 | Epic 1 | Nine Skip Reasons, AD-20 table order |
| CAP-5 | Epic 1 (rules), Epic 2 (pie→bar as asked) | Bar and Line only |
| CAP-6 | Epic 2 | Explicit Chart Request, FR-5 note |
| CAP-7 | Epic 1 | Title, axes, direction, placement, theme, 1280 px |
| CAP-8 | Epic 1 | `final`-only delivery, endpoint parity, time to first token |
| CAP-9 | Epic 1 | Not saved; reload shows Text Replies only |
| CAP-10 | Epic 1 | Server `chart_error` and browser fail-safe |
| CAP-11 | Epic 1 (entry), Epic 2 (note fields populated) | Chart Decision in `agent_trace` and UI trace panel |
| CAP-12 | Epic 3 | Evaluation dataset, runner, five passes |

## Epic List

### Epic 1: Priya sees the right Chart, or none, under her Text Reply
A comparison or trend question gets a Bar Chart (3–8 Categories) or Line Chart (3–12 Time Points) under the Text Reply, including the end-of-period balance trend; every other case gets the Text Reply alone, with one Skip Reason in the trace. Charts are titled and labelled, use the UI's colour tokens, arrive only in `final`, match across endpoints, are not saved, and can never break the Reply.
**CAPs covered:** CAP-1, CAP-2, CAP-3, CAP-4, CAP-5, CAP-7, CAP-8, CAP-9, CAP-10, CAP-11
**ADs:** AD-3, AD-4, AD-5, AD-6, AD-10, AD-11, AD-12, AD-13, AD-15, AD-17, AD-18, AD-19, AD-20, AD-22, AD-23, AD-24
**Notes:** `explicit_request` is always `False` in this epic, so no FR-5 note is due. Dead visualization code is removed in the story that wires `chart_builder` (AD-12). The AD-20 convention lands in one story and ends with the SQL cache clear.

### Epic 2: Priya can ask for a Chart in her own words
Chart wording on a transaction question is answered, never sent to fallback; a held-back Chart on an Explicit Chart Request ends the Text Reply with the fixed FR-5 note; a pie request with a fitting Result gets a Bar Chart with no mention of the substitution.
**CAPs covered:** CAP-6, CAP-5 (substitution as asked), CAP-11 (`explicit_request`, `note_appended`)
**ADs:** AD-3, AD-8, AD-21
**Depends on:** Epic 1.
**Why a separate epic (file-overlap check):** Epic 2 touches `chart_rules.py` and `chart_builder.py` again after Epic 1. Consolidation was considered and rejected: Epic 2 rests on the AD-21 `[ASSUMPTION]` that the supervisor classifies chart wording as BankSQL, which only the UJ-4 rows prove. Keeping it separate means a failed assumption reworks only Epic 2, and Epic 1 still demos cleanly.

### Epic 3: The team can measure chart correctness on the demo questions
About 30 labelled questions run against the live app across five passes, reporting SM-1..SM-6 and SM-C1/SM-C2, with the reference demo scenarios confirmed.
**CAPs covered:** CAP-12
**ADs:** AD-9, AD-22, AD-25
**Depends on:** Epic 1 (measures it alone); full targets need Epic 2.

## Epic 1: Priya sees the right Chart, or none, under her Text Reply

A comparison or trend question gets a Bar Chart or Line Chart under the Text Reply; every other case gets the Text Reply alone, with the Chart Decision and its Skip Reason visible in the trace. The Chart Path can never cost Priya her answer.

**Verification for every story (AGENTS.md):** there is no test suite and no pytest files go under `tests/data/`. Verify by starting `python src/app.py` from the repo root and calling `/process` (and `/process-stream` where stated). The seeded data ends 2026-06-13, so verification questions in this epic use absolute dates (for example "in May 2026", "in 2026 so far" read as Jan–Jun 2026) until Epic 3 shifts the data.

### Story 1.1: Chart Eligibility Rules as a pure module

As a presenter,
I want every Chart Decision to come from one fixed, code-only rule set,
So that the same Result always gets the same Chart or the same Skip Reason, and I can predict it on stage.

**Implements:** CAP-1, CAP-2, CAP-4, CAP-5, CAP-7 (title/axis content) · AD-17, AD-19, AD-20 (decision table and column constants), AD-10 (notice handling)
**Files:** new `src/utils/chart_rules.py`; changed `src/app_models.py`

**Acceptance Criteria:**

**Given** `src/app_models.py`
**When** the chart models are added
**Then** it defines `SkipReason` as one `Literal` of exactly the nine labels (`chart_error`, `fallback`, `no_transaction_data`, `empty_result`, `raw_list`, `too_few_points`, `unsuitable_shape`, `too_many_categories`, `too_many_time_points`), plus `ChartPoint(label: str, value: float)`, `ChartSpec(chart_type, title, x_label, y_label, points)` and `ChartResponse(decision, skip_reason, explicit_request, spec)`
**And** `ChartResponse` rejects a `skip_reason` unless `decision == "none"`, and rejects a `spec` unless `decision` is `bar` or `line`
**And** `Graph_State.chart_response: ChartResponse | None = None` is added, and `SubGraph_State` is not changed (AD-4)
**And** `app_models.py` still imports nothing project-internal

**Given** `src/utils/chart_rules.py`
**When** it is inspected
**Then** it uses only the stdlib (`ast`, `re`, `datetime`), makes no LLM, SQL or network call, and holds as constants the AD-20 label columns (`merchant`, `category`, `subcategory`, `period`), value columns (`spending`, `income`, `net`, `balance`), optional `period_start`/`period_end`, the per-transaction columns (`transaction_id`, `transaction_date`, `card_number`, `transaction_type`, `amount_usd`), the four `period` grain patterns (`%Y`, `%Y-%m`, `%G-W%V`, `%Y-%m-%d`) and the limits (3–8 Categories, 3–12 Time Points)
**And** it exposes one pure entry point that takes `database_query_results`, `user_query`, `banksql_queries` and `fallback_response` only (never the SQL text) and returns a `ChartResponse`

**Given** result rows as `list[str]` of `str(dict)`, possibly ending in the AD-10 `[System Notification: …]` string
**When** the rules parse them
**Then** the notice string is set aside first and recorded as `truncated`, and each row is parsed with `ast.literal_eval` inside the rules' own `try`

**Given** inputs matching each row of the AD-20 decision table
**When** the rules run
**Then** the first matching row wins, in this order: `fallback` (fallback set) → `no_transaction_data` (`banksql_queries` empty) → `empty_result` (None, empty, or all values None) → `raw_list` (per-transaction column present, or truncated without convention columns) → `too_few_points` (not truncated, 1–2 rows) → `unsuitable_shape` (not exactly one label + one value column, duplicate labels, non-numeric/None/non-finite value, bad or mixed-grain `period`) → `too_many_categories` (non-period label, truncated or > 8) → `too_many_time_points` (period label, truncated or > 12) → `bar` / `line`
**And** a Result with a `fallback_response` set and no rows reports `fallback`, not `empty_result`
**And** a Result grouped two ways (two label columns), or with both `spending` and `income` columns, reports `unsuitable_shape`
**And** "current balance" (1 row) and "income vs spending" (2 rows) shapes report `too_few_points`
**And** 3 and 8 Categories give `bar`, 2 and 9 give no Chart; 3 and 12 Time Points give `line`, 13 give no Chart; above the limit nothing is truncated, bucketed into "Other" or regrouped

**Given** a chartable Result
**When** the `ChartSpec` is built
**Then** the only `chart_type` values ever produced are `bar` and `line` (CAP-5)
**And** line points are sorted on the raw ISO `period` before display labels are formatted (for example `"Mar 2026"`, `"Wk 12 2026"`), and only the periods present are plotted; bar points keep Result order
**And** each `value` is finite, in USD, rounded to 2 decimals, and `net`/`balance` values may be negative
**And** the title names the measure and the period from the rules' tables (for example "Spending by category — October 2026"): a bar chart takes the period phrase from `period_start`/`period_end`, a line chart from its first and last period, and a bar chart without them gets no period phrase
**And** `y_label` names USD and `x_label` names the Category grouping or the Time Point period

**Given** a `ChartResponse`
**When** `note_due(chart_response)` is called
**Then** it returns true only when `explicit_request` is true, `decision == "none"` and `skip_reason != "chart_error"` (AD-21; no caller sets `explicit_request` true until Epic 2)

**Given** the app
**When** it is started with `python src/app.py` and `/process` is called with any transaction question
**Then** it starts and replies exactly as before (nothing calls the rules yet), and a throwaway REPL check of the rules against hand-built row lists for each decision-table row gives the expected outcome (no test file is committed)

### Story 1.2: chart_builder wired into the graph, response and trace, with dead visualization code removed

As a presenter,
I want every Reply to carry a Chart Decision in the envelope and the trace panel, with no leftover visualization path,
So that I can explain why any answer did or did not get a Chart, and nothing in the Chart Path can break the Text Reply.

**Implements:** CAP-4, CAP-8, CAP-9, CAP-10 (server), CAP-11 · AD-3, AD-5, AD-6, AD-7, AD-12, AD-13, AD-16, AD-17, AD-18, AD-19, AD-22, AD-24
**Files:** new `src/agents/chart_builder.py`; changed `src/agent_graph.py`, `src/app.py`, `src/app_setting.py`, `src/app_models.py`, `src/index.html` (trace panel and dead render path only), `requirements.txt`

**Acceptance Criteria:**

**Given** `src/agents/chart_builder.py`
**When** it is inspected
**Then** it is one async code-only stage `(state, logger, *extras)` that reads state, calls `chart_rules`, writes only `state.chart_response`, writes no `agent_metrics` entry and returns the state (AD-6, AD-24)
**And** it imports neither `app.py`, `agent_graph.py` nor any other agent

**Given** `src/agent_graph.py`
**When** the graph is compiled
**Then** a node `chart_builder` sits on plain edges `join → chart_builder → response_generator`, with no router after it and no new branch or supervisor label (AD-5, AD-17)
**And** compilation calls no node method that needs `self.logger` (AGENTS.md pitfall)

**Given** `src/app_setting.py`
**When** the settings are loaded
**Then** `CHART_FEATURE_ENABLED: bool = True` and `CHART_FAULT_INJECTION: bool = False` exist, overridable from `.env`, and reach the node from `_prepare_request` through `Agent_Graph` (stages never build `AppSettings`)
**And** with `CHART_FEATURE_ENABLED=false`, `chart_response` stays `None`: no Chart and no `chart_builder` trace entry

**Given** `CHART_FAULT_INJECTION=true`
**When** any question is sent to `/process`
**Then** `chart_builder` raises inside its own `try`, logs the exception and sets `chart_response` from literal constants (`decision="none"`, `skip_reason="chart_error"`, `explicit_request=False`, `spec=None`)
**And** `fallback_response` is untouched, the request is not routed to `fallback`, the trace has no `fallback_agent` entry, and the Reply carries a normal Text Reply with no fallback or technical-error text (AD-18, CAP-10; the LLM rewords between runs, so "unchanged" means same path and same kind of answer, not identical text)

**Given** a shared envelope helper in `src/app.py` used by both `/process` and `/process-stream`
**When** a Reply is built
**Then** `visualization` is `ChartSpec.model_dump()` when `decision` is `bar` or `line` and `response_generation_response` exists, and `null` otherwise, so no Chart ever accompanies the technical-error message
**And** the envelope keys stay exactly `{response, visualization, agent_trace}` and no endpoint, envelope key or SSE event type is added (AD-13, AD-22)

**Given** `_build_agent_trace`
**When** `chart_response` is set
**Then** one entry is added just before the `response_generator` entry with `agent: "chart_builder"`, `title: "Chart"`, `status` (`ok` for a Chart, `skipped` for no chart, `error` for `chart_error`), a one-line `summary`, `details: {decision, skip_reason, explicit_request, note_appended, point_count, title}` with `note_appended = note_due(chart_response)`, and `metrics: null`

**Given** `/process-stream`
**When** a question is streamed
**Then** `chart_builder` is registered in `_STREAM_STEP_LABELS` as `None` (silent, no `step` event) and is not on the token allowlist
**And** the Chart travels only in the `final` event, and the streamed tokens are unchanged

**Given** the dead visualization code listed in the spine Structural Seed
**When** this story is done
**Then** `VisualizationDecision`, `VisualizationResponse` and `Graph_State.visualization_response` are removed from `app_models.py`; the `visualization_agent` label, trace block and envelope logic are removed from `app.py`; the `visualization_agent` icon, its `buildDetails` branch, the `plotly_json` render path and the "Chart could not be rendered." placeholder are removed from `index.html`; and the unused Python `plotly` package is removed from `requirements.txt`
**And** `index.html` has a `chart_builder` trace icon and a `buildDetails` branch showing the decision, Skip Reason and point count
**And** a repo-wide search finds no `visualization_agent`, `visualization_response`, `should_visualize` or `plotly_json` (one id, no aliases — AD-12)

**Given** the running app
**When** the same questions are sent to `/process` and `/process-stream` — a help question ("How do I reset my PIN?"), an off-topic question, a last-10-transactions question and a spending-by-category question
**Then** both endpoints give the same `chart_builder` trace entry and the same `visualization` value; the help question reports `no_transaction_data`, the off-topic question `fallback`, the transaction list `raw_list`
**And** no `chat_messages`, Redis history or `request_metrics` row contains the Chart or the Chart Decision (CAP-9, AD-22)
**And** the `/process-stream` time to first token is not noticeably changed (measured formally in Epic 3)

### Story 1.3: Result-shape SQL convention and the balance trend, ending with a SQL cache clear

As Priya,
I want my comparison and trend questions, including "Show my balance trend", to come back in a shape the rules can chart,
So that I see a Bar Chart of my categories, a Line Chart of my monthly spending, and a Line Chart of my end-of-period balance.

**Implements:** CAP-1, CAP-2, CAP-3, CAP-4 · AD-8, AD-9, AD-10, AD-11, AD-20
**Files:** changed `src/agents/sql_execution.py`, `prompts/text2sql_generator.yml`, `prompts/sql_correction.yml`; `src/utils/chart_rules.py` constants cross-checked (from Story 1.1)
**Sequencing:** the AD-20 convention spans `text2sql_generator.yml`, `sql_correction.yml` and `chart_rules.py`. `chart_rules` landed first (Story 1.1); this story lands both prompts together, and its last step is the SQL cache clear.

**Acceptance Criteria:**

**Given** `sql_execution`
**When** a `SELECT` query contains `OVER (` or `GROUP BY` and no `LIMIT`
**Then** it is treated as an aggregate and gets no `LIMIT 20` / `ORDER BY transaction_date DESC` rewrite (AD-10)
**And** any result longer than 20 rows is still cut to 20 with the notice string appended, and plain detail queries are still limited as before

**Given** `prompts/text2sql_generator.yml`
**When** the convention is added
**Then** it instructs that comparison and trend answers output exactly one label column (`merchant`, `category`, `subcategory` or `period`) and exactly one value column (`spending`, `income`, `net` or `balance`, whose name carries the direction), optionally `period_start`/`period_end` for a bar chart's date range
**And** `period` is built only with `strftime` (`'%Y'`, `'%Y-%m'`, `'%G-W%V'`, `'%Y-%m-%d'`), never from the integer `year`/`month` columns
**And** comparison and trend queries never add a `LIMIT` or a top-N, and list answers never use the convention's column names
**And** the balance-trend guidance takes, for each period, the `balance` of the last row by `(transaction_date, transaction_id)` descending, ends in `ORDER BY period`, and never returns one row per transaction
**And** every new instruction and example applies `customer_id = '<customer_id>'` in every subquery and CTE, and no existing customer-scoping instruction is removed or weakened (AD-11)
**And** the column names in the prompt are exactly the constants in `chart_rules.py`

**Given** `prompts/sql_correction.yml`
**When** it is updated
**Then** its schema matches the `transactions` table (no `country` or `card_type`; includes `transaction_type` and `balance`), and it instructs the model to keep the output column names of the SQL it repairs

**Given** the prompt changes are in place
**When** the story's final step runs
**Then** `DELETE FROM query_cache` has been run on `transactions.db` and every Redis `qcache:*` key deleted (or Redis confirmed absent), before any verification question is sent

**Given** the running app with the cache cleared
**When** `/process` is called with absolute-date questions against the seeded data
**Then** "How much did I spend by category in May 2026?" for a customer with 3–8 Categories gets `visualization.chart_type == "bar"` with a title naming the period
**And** "How has my monthly spending changed from January to June 2026?" gets a `line` with one point per month present, in time order
**And** "Show my balance trend from January to June 2026" gets a `line` of end-of-month balances whose values equal the `balance` of each month's last transaction by `(transaction_date, transaction_id)`, checked against the DB by hand
**And** the same balance question for two different customers returns each customer only their own series
**And** "Daily spending across May 2026" reports `too_many_time_points` and is not regrouped into weeks
**And** a spending-by-merchant question with more than 8 merchants reports `too_many_categories`, and a monthly series of 13 or more points reports `too_many_time_points`
**And** asking the same question a second time (warm cache) gives the same Chart Decision

### Story 1.4: Chart drawn under the Text Reply, fail-safe in the browser

As Priya,
I want the Chart to appear directly under its Text Reply, in FinTell's colours, and never break the page,
So that the shape of my money is clear at a glance and my answer is always there.

**Implements:** CAP-7 (placement, theme, width), CAP-9, CAP-10 (browser) · AD-15, AD-23
**Files:** changed `src/index.html`

**Acceptance Criteria:**

**Given** `index.html`
**When** the page loads
**Then** Plotly.js 2.35.2 is loaded from the CDN with `defer`, so an unreachable CDN never blocks the page

**Given** a `final` payload whose `visualization` is a `ChartSpec`
**When** the Reply is rendered
**Then** the Text Reply is rendered first and the Chart is drawn directly under it as a Plotly bar or line trace mapped from `chart_type`, `title` (in `{text: …}` form), `x_label`, `y_label` and `points`
**And** the x-axis is `type: "category"` so labels are never re-parsed as dates, and points are drawn in the order received (the UI never sorts or parses labels)
**And** colours come from the existing `:root` CSS tokens, read at draw time; the backend sends no colours or layout
**And** the Chart fits the chat column at 1280 px browser width with no horizontal scrolling
**And** net or balance values below zero are plotted as they are

**Given** a `visualization` that is `null`, or a spec that fails validation (`chart_type` not `bar`/`line`, fewer than 3 points, a non-string label, a non-finite value, or missing fields)
**When** the Reply is rendered
**Then** only the Text Reply is shown: no Chart, no placeholder text, and no uncaught error in the console

**Given** `window.Plotly` is not available (CDN blocked in DevTools)
**When** a charted Reply arrives
**Then** the Text Reply shows, no Chart is drawn, and the page keeps working for the next question

**Given** a conversation with charted Replies
**When** the page is reloaded and the conversation is restored from history
**Then** earlier Replies show their Text Replies only, with no Chart, error or placeholder (CAP-9)

**Given** the running app
**When** the Story 1.3 bar, monthly-line and balance-line questions are asked in the UI
**Then** each Chart appears under its Text Reply after the text has finished streaming, and the trace panel's `chart_builder` row matches what is drawn

## Epic 2: Priya can ask for a Chart in her own words

Chart wording on a transaction question is answered as a transaction question, never sent to fallback. When the Chart Eligibility Rules hold the Chart back on an Explicit Chart Request, the Text Reply ends with one fixed, plain line saying why. A requested pie chart becomes a Bar Chart when the Result fits, without comment.

**Verification for every story:** as in Epic 1 — run `python src/app.py` and call `/process` (and `/process-stream` where stated), with absolute-date questions until Epic 3 shifts the data. No test files are committed.

### Story 2.1: Explicit Chart Requests detected and answered as transaction questions

As Priya,
I want "Chart my spending by merchant" or "Give me a pie chart of my spending by category" to be answered like any other question about my transactions,
So that asking for a visual never gets my question rejected as off-topic, and I get the Chart the rules allow.

**Implements:** CAP-5 (substitution as asked), CAP-6 (detection and routing), CAP-11 (`explicit_request` in the trace) · AD-8, AD-17, AD-20 (cache key), AD-21
**Files:** changed `src/utils/chart_rules.py`, `src/agents/chart_builder.py`, `prompts/suprivisor_prompt.yml` (misspelling kept)

**Acceptance Criteria:**

**Given** `chart_rules.EXPLICIT_CHART_PATTERN`
**When** it is matched case-insensitively, on whole words, against a `user_query`
**Then** it matches the word forms of chart, graph, plot and visualise/visualize (for example `chart`, `charts`, `charting`, `graphed`, `plotted`, `visualisation`, `visualize`)
**And** it matches a chart type (`bar`, `line`, `pie`, `scatter`, `area`, `donut`, `histogram`) when followed by chart/graph/plot, or preceded by "as a"/"as an" (for example "pie chart of…", "show it as a bar")
**And** it does not match inside other words (`Chartered`, `online`), nor "show my…" or "trend" on their own
**And** a negated phrase ("no chart", "without a graph", "don't plot") is not an Explicit Chart Request `[ASSUMPTION AD-21]`
**And** the pattern never extracts or records which chart type was asked for (CAP-5)

**Given** `chart_builder`
**When** it builds `chart_response`
**Then** `explicit_request` is set from `EXPLICIT_CHART_PATTERN` over `input_request.user_query`, and nothing else about the Chart Decision changes: the decision table still decides (CAP-6)
**And** on the `chart_error` path `explicit_request` stays the literal `False` (AD-18)
**And** the `chart_builder` trace entry's `details.explicit_request` reflects it

**Given** `prompts/suprivisor_prompt.yml`
**When** it is updated
**Then** chart wording on a transaction question is classified `BankSQL`, and the presentation words ("chart", "pie chart of", "as a bar", …) are stripped from the `sub_query`
**And** the guardrail prompt is unchanged, and no new supervisor class label or branch is added (AD-5)

**Given** the supervisor prompt changed
**When** the story's last configuration step runs
**Then** `DELETE FROM query_cache` has been run and the Redis `qcache:*` keys deleted, because the cache key hashes `str(banksql_queries)`

**Given** the running app with the cache cleared
**When** `/process` is called with "Chart my spending by merchant in 2026" and "Give me a pie chart of my spending by merchant in 2026" for a customer with more than 8 merchants
**Then** both are answered with a normal Text Reply, never routed to `fallback`; the trace shows `decision: "none"`, `skip_reason: "too_many_categories"`, `explicit_request: true`
**And** "Pie chart of my spending by category in May 2026" for a customer with 3–8 Categories gets `visualization.chart_type == "bar"`, and the Text Reply does not mention pie charts or a substitution
**And** "What's my spending by category in May 2026 — no chart please" gives `explicit_request: false`, and a question mentioning "Barclays Chartered" or "online payments" gives `explicit_request: false`
**And** if any UJ-4 phrasing is still blocked by the unchanged guardrail, that is recorded as a finding against the AD-21 assumption rather than worked around in code

### Story 2.2: The FR-5 note explains a held-back Chart

As Priya,
I want one short, plain line telling me why no Chart was shown when I asked for one,
So that I'm not left wondering whether my request was ignored, without being bothered when I didn't ask.

**Implements:** CAP-6 (note), CAP-8 (note not streamed), CAP-11 (`note_appended`) · AD-3, AD-8, AD-21, AD-22
**Files:** changed `src/utils/chart_rules.py`, `src/agents/response_generator.py`, `prompts/response_generator_prompt.yml`

**Acceptance Criteria:**

**Given** `chart_rules.CHART_NOTES`
**When** it is inspected
**Then** it holds one fixed, plain string for each of the nine `SkipReason` values (for example `too_many_categories` → "There are too many merchants or categories to chart clearly."), keyed by every `SkipReason` value, and no other module holds note text

**Given** `response_generator`
**When** it assembles `response_generation_response`
**Then** as its last step, outside every branch, it appends `CHART_NOTES[skip_reason]` when `chart_rules.note_due(state.chart_response)` is true, and adds nothing otherwise
**And** the note is added on the SQL, help-only and fallback paths alike, after the fallback message, help answer and SQL rephrase (AD-3)
**And** it makes no extra LLM call, and `chart_response` being `None` (feature off) adds nothing

**Given** `prompts/response_generator_prompt.yml`
**When** it is updated
**Then** it tells the model never to mention charts, graphs or visuals, because the note is written by code

**Given** the running app
**When** "Chart my spending by merchant in 2026" (more than 8 merchants) is sent to `/process`
**Then** the Text Reply ends with exactly the `too_many_categories` note, once, and the trace shows `note_appended: true`
**And** "How do I reset my PIN? Show it as a chart" ends with the `no_transaction_data` note
**And** the same merchant question without chart wording gets no note and `note_appended: false`
**And** with `CHART_FAULT_INJECTION=true`, the explicit question gets no note (`chart_error`, `note_appended: false`) and still gets a normal Text Reply with no fallback or technical-error text

**Given** `/process-stream`
**When** the explicit merchant question is streamed
**Then** the note is not among the streamed `token` events; it appears in the `final` event's `response`, which replaces the streamed text, and the `final` payload matches `/process`

**Given** a conversation in which a note was appended
**When** the chat history is read back (Redis history or `chat_messages`, and the reloaded UI)
**Then** the note is saved as part of that Text Reply, while the Chart and Chart Decision are still not saved (CAP-6, CAP-9)

## Epic 3: The team can measure chart correctness on the demo questions

About 30 labelled questions run against the live app across five passes and report SM-1..SM-6 plus SM-C1/SM-C2, and the reference demo scenarios are confirmed before the demo. The dataset is data only; the runner lives outside `tests/data/` and never imports app code.

### Story 3.1: Demo data brought up to date and transactions.db rebuilt

As a presenter,
I want the seeded transactions shifted so that "last month" and "this year" have data,
So that the demo questions and the evaluation dataset can use relative dates and get the expected Charts.

**Implements:** CAP-12 (precondition), CAP-3 (tie-break re-check) · AD-9, AD-25
**Files:** none changed in code; regenerates `data/data_processing/df_transaction_data_enhanced.xlsx` and `transactions.db`

**Acceptance Criteria:**

**Given** the seeded data ends 2026-06-13
**When** `python data/data_processing/transaction_data_enhance.py shift` is run
**Then** the enhanced `.xlsx` is rewritten with dates ending near today, and the new last transaction date is recorded in the story's completion notes

**Given** the shifted `.xlsx`
**When** `python src/clients/sqllite_db_creation.py --db transactions.db --xlsx data/data_processing/df_transaction_data_enhanced.xlsx` is run with the app stopped
**Then** `transactions.db` is replaced (the old copy is kept with a timestamp), the `transactions` schema is unchanged, and the wipe of `chat_*`, `request_metrics` and `query_cache` is accepted as expected
**And** the Redis `qcache:*` keys are deleted as well, because the rebuild does not touch Redis and cached SQL would otherwise survive

**Given** the rebuilt DB
**When** the FR-15 tie-break is re-checked (for every customer, ordering rows by `(transaction_date, transaction_id)` reproduces the running `balance`)
**Then** every row matches (3950/3950 on the pre-shift DB) and the count is recorded; any mismatch blocks Epic 3 and is raised against AD-9

**Given** the app started with `python src/app.py`
**When** `/process` is called with "How much did I spend by category last month?" and "Show my balance trend this year" for the demo customer
**Then** both return non-empty Results, a `bar` and a `line` respectively

### Story 3.2: Labelled chart evaluation dataset

As a developer,
I want a labelled set of about 30 questions that covers every Chart Eligibility Rule and every demo scenario,
So that chart correctness is measured against fixed expectations rather than judged by eye.

**Implements:** CAP-12 (and the labels for CAP-1..CAP-6, CAP-11) · AD-20 (labels follow the decision table), AD-21, AD-25
**Files:** new `tests/data/chart_eval_dataset.xlsx` (data only; no pytest or Python files added under `tests/data/`)

**Acceptance Criteria:**

**Given** `tests/data/chart_eval_dataset.xlsx`
**When** it is opened
**Then** it has exactly the columns `question`, `customer_id`, `expected_chart` (`bar`/`line`/`none`), `expected_skip_reason` (a `SkipReason` or empty when a Chart is expected), `explicit_request` (bool) and `expect_note` (bool), with about 30 rows
**And** `expect_note` is true exactly when `explicit_request` is true and `expected_chart` is `none` (the `chart_error` case is covered by the fault-injection pass, where every row expects `chart_error` and no note)

**Given** the dataset
**When** its coverage is checked
**Then** it contains every reference scenario in `evaluation-plan.md` (UJ-1, both UJ-2 Charts, the ~30-day daily series, the three UJ-3 questions, the three UJ-4 phrasings, income vs spending), worded as there
**And** it covers every Skip Reason other than `chart_error` with at least one row; both sides of each limit (2/3 and 8/9 Categories, 12/13 Time Points); a Result with more than 20 periods; a mixed SQL-and-help question; a combined question with several SQL Sub-queries; a mixed-direction Result; pie-to-bar substitution; the FR-15 balance trend for two different customers; and Explicit Chart Request positives, inside-word negatives (`Chartered`, `online`) and negations
**And** each label was set by checking the shifted DB from Story 3.1 by hand (for example, counting the customer's Categories or months), following the AD-20 decision-table order

**Given** date-dependent labels (for example "11 months this year")
**When** a future `shift` is run
**Then** a note in the dataset (a second sheet) records the data end date the labels were set against and says to re-label after each shift

### Story 3.3: Chart evaluation runner

As a developer,
I want one script that runs the dataset against the live app for any pass and computes the metrics,
So that each evaluation run is reproducible and isolated, and reports SM-1..SM-6 and the counter-metrics the same way every time.

**Implements:** CAP-8 (measurement), CAP-10 (measurement), CAP-12 · AD-16, AD-22, AD-25
**Files:** new `scripts/eval_charts.py` (outside `tests/data/`)

**Acceptance Criteria:**

**Given** `scripts/eval_charts.py`
**When** it is inspected
**Then** it is a plain script that imports no app code (nothing from `src/`), talks to the app only over HTTP, and takes the base URL, dataset path, pass name and an output directory outside `tests/data/` as arguments

**Given** a `clear-cache` command
**When** it is run before the cold pass
**Then** it runs `DELETE FROM query_cache` on `transactions.db` and deletes every Redis `qcache:*` key (skipping Redis cleanly when it is absent)

**Given** a pass name of `cold`, `warm`, `stream`, `fault` or `off`
**When** the runner runs it against an app started with `HISTORY_LOOKBACK_HOURS=0` (and, for `fault` / `off`, with `CHART_FAULT_INJECTION=true` / `CHART_FEATURE_ENABLED=false`)
**Then** each request uses a fresh `session_id`, at most 18 requests are sent per minute, and an HTTP 429 is retried without counting the retry as a result
**And** `cold`, `warm` and `fault` call `/process`; `stream` calls `/process-stream` and records time to first `token`; `off` calls both endpoints so latency and time to first token have a baseline
**And** for each row it stores the Text Reply, end-to-end latency, the `chart_builder` trace entry, `visualization.chart_type` and whether the trace has a `sql_correction` entry, reading nothing else from the envelope
**And** the `fault` pass flags the run as invalid if any row lacks `chart_error`, and the `off` pass flags it invalid if any row has a `chart_builder` entry (guards against the app being started with the wrong flags)

**Given** stored pass outputs
**When** the `report` command runs
**Then** it computes SM-1 (no-chart rows with no Chart and the note exactly when expected), SM-2 (chart rows with the labelled type), SM-3 (median end-to-end latency on vs off, split by charted / un-charted, and median time to first token on vs off), SM-4 (`cold` vs `stream` Chart Decision match, excluding the AD-7/AD-13 as-is paths), SM-5 (rows in `fault` whose Text Reply is missing, is the technical-error or fallback text, or has a fallback trace entry the `warm` row lacks), SM-6 (Skip Reason match), SM-C1 (chart rate vs share labelled "chart") and SM-C2 (Text Reply length on vs off)
**And** it reports the cold/warm Chart Decision stability, skipping rows whose trace has a `sql_correction` entry
**And** it prints each metric against its target (SM-1 100%, SM-2 ≥ 90%, SM-3 ≤ 2 s / ≤ 200 ms, SM-4 100%, SM-5 0, SM-6 ≥ 90%) and lists every failing row with its question, expected and actual outcome

### Story 3.4: Full evaluation run and demo-readiness check

As a presenter,
I want the five passes run and the demo scenarios checked in the UI,
So that I know before going on stage that the right Charts appear, the wrong ones don't, and no answer can be lost.

**Implements:** CAP-7, CAP-9 (manual checks), CAP-12, and the SPEC success signal · AD-25
**Files:** new run report in `_bmad-output/implementation-artifacts/` (the raw pass outputs stay in the runner's output directory, outside `tests/data/`)

**Acceptance Criteria:**

**Given** Stories 3.1–3.3 are done and Epics 1–2 are implemented
**When** the passes are run in order — `clear-cache`, `cold`, `warm`, `stream`, then `fault` and `off` with the app restarted with the matching flags each time
**Then** the `report` output is saved in the run report with the date, the git commit and the data end date

**Given** the report
**When** it is reviewed
**Then** SM-1 is 100%, SM-2 ≥ 90%, SM-4 100%, SM-5 is 0, SM-6 ≥ 90% and SM-3 is within target, or each miss is recorded as a finding with its failing rows; labels are changed only for date staleness after a shift, never to make a row pass
**And** SM-C1 and SM-C2 are reported, and the SM-C2 check includes a hand spot-check of at least five Text Replies with the feature on and off

**Given** the running app in the browser at 1280 px
**When** the reference scenarios are asked in the UI
**Then** the presenter sees a Bar Chart for spending by category with the month in the title, Line Charts for the monthly spending and balance trends, no Chart on the single balance, transaction list, help question and 30-day daily series, and the one-line note on a refused explicit request (SPEC success signal)
**And** each Chart fits the chat column with no horizontal scrolling, and after a reload earlier Replies show Text Replies only (CAP-7, CAP-9)

**Given** the spine assumptions listed in the SPEC (AD-12 silent node, AD-17 flags, AD-18 fault injection, AD-20 column convention, AD-21 negation and supervisor classification, AD-25 isolation, Plotly.js 2.35.2, runner path)
**When** the run is complete
**Then** the run report states, for each, whether the run confirmed it or a finding was raised against it
