---
id: SPEC-transaction-charts
companions:
  - ../../planning-artifacts/architecture/architecture-Agentic_BChatbot-2026-11-22/ARCHITECTURE-SPINE.md
  - glossary.md
  - evaluation-plan.md
sources:
  - ../../planning-artifacts/prds/prd-Agentic_BChatbot-2026-11-22/prd.md
  - ../../planning-artifacts/prds/prd-Agentic_BChatbot-2026-11-22/addendum.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# FinTell — Transaction Charts

The architecture spine (adopted companion) defines **how**: node placement, state fields, the Result-shape convention, the decision table, the envelope and UI rules (AD-1 to AD-25, IDs stable). This SPEC defines **what** and **when**. Capitalised terms come from `glossary.md` and are used exactly. Where the spine and the sources differ, the spine governs.

## Why

A **vision to realize** for an **internal demo**. FinTell answers comparison and trend questions about a customer's transactions in text only, so the user has to picture the shape of their money in their head. A simple Bar Chart or Line Chart under the Text Reply shows that shape at a glance, but only where it makes the answer clearer: a Chart of one total, a Raw Transaction List or 30 squeezed bars is worse than none. The feature is defined as much by when it holds back as by when it draws. The presenter must see the right Chart on the demo questions and never see a Chart where one doesn't belong, or one that breaks a reply.

## Capabilities

- **CAP-1** — Bar Chart for Category comparisons (FR-1)
  - **intent:** A user who asks how an amount splits across Categories sees a Bar Chart under the Text Reply, but only when there are 3 to 8 Categories.
  - **success:** Results with 3 or 8 Categories get a Bar Chart; 2 or 9 get none. Above 8 there is no top-N, "Other" bar or regrouping.

- **CAP-2** — Line Chart for Time Point trends (FR-2)
  - **intent:** A user who asks how an amount moves over time sees a Line Chart, but only when there are 3 to 12 Time Points.
  - **success:** 12 monthly Time Points get a Line Chart; 13 get none. Daily rows across a whole month get none and are not regrouped into weeks. Points plot in time order whatever the row order, and only the Time Points present are plotted (no gap filling).

- **CAP-3** — Balance trend (FR-15)
  - **intent:** A user who asks how their balance changed over a period gets the end-of-period balance for each Time Point, charted as a Line Chart.
  - **success:** "Show my balance trend this year" with 11 months of data gives a Line Chart of 11 end-of-month balances. Each value is the `balance` of the period's last transaction (tie-break per AD-20). A balance trend never comes back as one row per transaction. More than 12 Time Points gives no Chart. The same question for two customers gives each customer only their own series.

- **CAP-4** — Holding back (FR-3, FR-6, FR-7, FR-8)
  - **intent:** When a Chart would not help, the user gets only the Text Reply, and each "no chart" Chart Decision carries exactly one Skip Reason.
  - **success:** Each of the nine Skip Reasons gives no Chart. Where cases overlap, the first matching row of the AD-20 decision table wins (for example, an empty Result after a fallback reports `fallback`). Fixed cases: a Result grouped two ways, a Result mixing spending and income that isn't net or balance, and a combined multi-SQL-Sub-query Result that doesn't fit one shape all give `unsuitable_shape`. Help-centre content is never charted. A non-raw Result of 1–2 rows ("current balance", "income vs spending") gives `too_few_points`.

- **CAP-5** — Bar and Line only (FR-4)
  - **intent:** The user never sees any chart type other than Bar or Line, whatever they ask for.
  - **success:** Pie, scatter, area and other requested types are never produced. "Pie chart of spending by category" with 5 Categories gets a Bar Chart, and the Text Reply doesn't mention the substitution.

- **CAP-6** — Explicit Chart Request (FR-5)
  - **intent:** A user who explicitly asks for a chart of their transactions is answered as a normal transaction question. The Chart Eligibility Rules still decide whether a Chart is shown, and a refusal is explained in one plain line.
  - **success:**
    - "Chart my spending by merchant" with 14 merchants gives no Chart and is never sent to fallback as off-topic.
    - With an Explicit Chart Request and a "no chart" decision, the Text Reply ends with the fixed note for that Skip Reason. The note is left out for `chart_error`.
    - Without an Explicit Chart Request, a "no chart" decision adds nothing to the text.
    - The note is saved with chat history as part of the Text Reply.

- **CAP-7** — Chart content and placement (FR-8, FR-9)
  - **intent:** A Chart can be read without explanation and looks like part of the FinTell chat.
  - **success:**
    - Every Chart has a title that names the measure and the period (for example "Spending by category — October 2026").
    - The value axis is labelled in USD; the other axis names the Category grouping or the Time Point period.
    - A Chart shows a single direction (spending or income), except net and balance, which may go negative and are plotted as they are.
    - The Chart renders directly under its Text Reply, in the UI's existing colour tokens, and fits the chat column at 1280 px without horizontal scrolling.

- **CAP-8** — Streaming and endpoint parity (FR-10, FR-11)
  - **intent:** On `/process-stream` the Text Reply streams as it does today and the Chart arrives after the text. `/process` and `/process-stream` give the same Chart Decision for the same request.
  - **success:**
    - Median time to first token changes by no more than 200 ms.
    - The Chart arrives only in the `final` event. No Chart Path output is streamed as tokens, and no new event type is added.
    - Every evaluation question gives the same Chart Decision on both endpoints, with the same empty `visualization` value when there is no Chart.

- **CAP-9** — Charts not saved (FR-12)
  - **intent:** Charts exist only for the live Reply. Reloading a past conversation shows Text Replies only.
  - **success:** After a page reload, earlier Replies show no Chart, no error and no placeholder.

- **CAP-10** — Fail-safe Chart Path (FR-13)
  - **intent:** No failure on the Chart Path, server or browser, can cost the user their answer.
  - **success:**
    - On the server, an injected Chart Path error gives the normal Text Reply, no Chart, Skip Reason `chart_error`, and no fallback routing or fallback text.
    - In the browser, malformed or partial chart data, or a chart library that fails to load, shows the Text Reply with no Chart and no error that breaks the page.

- **CAP-11** — Traceable Chart Decision (FR-14)
  - **intent:** A presenter or developer can see why each Reply did or did not get a Chart.
  - **success:** Every Reply's `agent_trace`, and the UI trace panel, show the Chart Decision: the chart type, or "no chart" with its Skip Reason. The Chart Decision is not saved anywhere. A supervisor error that ends with no Reply at all (AD-7 as-is defect) is exempt from CAP-4 and CAP-11 until that defect is fixed.

- **CAP-12** — Measurable correctness (PRD §7)
  - **intent:** The team can measure chart correctness, latency, parity and fail-safety by running a labelled evaluation dataset against the running app.
  - **success:** A run of about 30 labelled questions in `tests/data/`, using the passes in `evaluation-plan.md`, reports SM-1 to SM-6 and the counter-metrics. The cold and warm passes give the same Chart Decision, excluding rows that went through `sql_correction`.

## Constraints

- **The Text Reply always comes first.** It is the primary answer; a Chart never replaces it, delays it or breaks it. The Text Reply changes in only two ways: the FR-5 note (CAP-6) and the end-of-period balance answer (CAP-3).
- **Code-only decision.** The Chart Decision is made in code, deterministically, before `response_generator` runs, with no LLM, SQL or network call (AD-17). This is what keeps CAP-8's 200 ms limit and lets SM-1 reach 100%.
- **Reads only the Result.** The Chart Path uses only the request's one Result plus `user_query`, `banksql_queries` and `fallback_response`. It makes no extra queries, fills no gaps, does no regrouping and does no top-N (AD-20, AD-11).
- **The chart stage is optional.** Its failure never writes `fallback_response` (AD-18).
- **The SQL column convention signals the Result's shape** (AD-20). The convention changes together in three places: the text2sql prompt, the sql_correction prompt and `chart_rules`. After any change, clear `query_cache` and the Redis `qcache:*` keys.
- **Customer scoping.** Every new piece of SQL guidance or example applies the `customer_id` filter in every subquery and CTE (AD-11).
- **No new endpoint, envelope key or SSE event type.** The `visualization` key carries a `ChartSpec` or `null` (AD-13, AD-22).
- **Data and dead code.** The `transactions` schema and data don't change. The unused visualization code is removed, not reused (spine Structural Seed).
- **Demo stakes.** This is an internal demo on a single local instance with seeded data; NFRs are sized to that (AD-14).
- **Evaluation data stays data.** The evaluation dataset in `tests/data/` holds no pytest files, and its runner lives outside that folder.
- **Data dates.** The seeded data ends 2026-06-13. Before a demo or evaluation, run `shift` and rebuild `transactions.db`, or write questions with absolute dates.

## Non-goals

- Chart types other than Bar and Line: no pie, scatter, area, stacked, multi-series or combo charts.
- Making a Result chartable by top-N truncation, "Other" buckets, filling missing periods or automatic regrouping.
- Charts of help-centre content.
- Interactive analysis: no drill-down, filtering, chart editing or user-chosen axes. Default hover tooltips are allowed but not required.
- Exporting or downloading charts, and dashboards.
- Saving Charts with chat history (revisit if the demo needs to replay past conversations).
- Running each SQL Sub-query separately so each can be charted.
- Phone-width layout, and a dark theme.
- Code-level customer isolation hardening (deferred in the spine; AD-11 stays as-is).

## Success signal

- On the demo questions (`evaluation-plan.md`, reference scenarios), the presenter sees:
  - a Bar Chart for spending by category
  - Line Charts for the monthly spending and balance trends
  - no Chart on a single balance, a transaction list, a help question or a 30-day daily series
  - the one-line note on a refused explicit request
- A full evaluation run shows:
  - SM-1 (no-chart correctness) at 100% and SM-2 (chart correctness) at 90% or more
  - SM-4 (parity across the two endpoints) at 100%
  - SM-5: zero lost or changed Text Replies under fault injection

## Assumptions

- The spine's `[ASSUMPTION]` calls are built against as written:
  - AD-12: `chart_builder` is a silent step
  - AD-17: the `CHART_FEATURE_ENABLED` and `CHART_FAULT_INJECTION` flags
  - AD-18: how fault injection works
  - AD-20: the SQL column convention
  - AD-21: negated phrases ("no chart") are not explicit requests, and the supervisor classifies chart wording as BankSQL
  - AD-25: evaluation-run isolation
  - Plotly.js 2.35.2 is kept
  - the runner lives at `scripts/eval_charts.py`

  Revisit them at the first evaluation run.
- There is no chart accessibility requirement beyond the Text Reply carrying the full answer. The PRD is silent on this, and the stakes are a demo.
- The spec slug is `transaction-charts` rather than the PRD folder's `Agentic_BChatbot`, which names the project rather than the feature.

## Open Questions

- Should a missing period be drawn as zero instead of being omitted? This is PRD OQ-3, owned by UX. The default in force is to omit it with no gap filling (CAP-2).
