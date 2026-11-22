---
title: FinTell — Transaction Charts
status: final
created: 2026-11-22
updated: 2026-11-22
baseline: _bmad-output/planning-artifacts/architecture/architecture-Agentic_BChatbot-2026-11-22/ARCHITECTURE-SPINE.md
---

# PRD: FinTell — Transaction Charts
*Working title — confirm.*

## 0. Document Purpose

This PRD is for whoever builds the chart feature in FinTell, the banking-transactions chatbot. Its next readers are the `bmad-architecture` and `bmad-create-epics-and-stories` workflows. It describes **what** the feature does and **when**. **How** it is built (graph wiring, state fields, envelope shape) is left to architecture. Technical notes collected along the way are in `addendum.md`.

The PRD builds on the baseline architecture spine (`ARCHITECTURE-SPINE.md`, FinTell at `3bac867`) and does not restate it. Where a requirement touches a spine rule (AD-n), it says so. Vocabulary comes from §3 Glossary and is used exactly. Product tensions are flagged inline as `[NOTE FOR PM]`. There are no open assumptions (§9).

Stakes: **internal demo**, single instance, local machine, seeded data. Rigor and NFRs are set to that level.

## 1. Vision

When someone asks FinTell how their spending compares across categories or how it changed over time, they get a Text Reply they have to picture in their head. This feature puts a simple Bar Chart or Line Chart under the Text Reply, **only when a chart makes the answer clearer**.

The feature is defined as much by when it holds back as by when it draws. A Chart of one total, a Raw Transaction List, or 30 squeezed daily bars is worse than no Chart. So FinTell follows a short, predictable rule set: Bar Charts for comparisons across Categories, Line Charts for trends over Time Points, and nothing otherwise. The Text Reply is always the main answer. The Chart adds to it and never replaces it, delays it or breaks it.

For the demo, that means a presenter never sees a Chart where one doesn't belong, and sees the right Chart on the demo questions.

## 2. Target User

### 2.1 Jobs To Be Done

- **Functional:** "When I ask how my money is split or how it's moving, show me the shape at a glance so I don't have to compare numbers in a sentence."
- **Functional:** "When a chart wouldn't help, don't make me scroll past one."
- **Contextual (presenter):** "When I demo FinTell, the charts show up on the questions where they impress and never on the ones where they'd look silly or break."

### 2.2 Non-Users (v1)

- Analysts who want to explore, slice or export data. FinTell is not a BI tool.
- Anyone wanting charts of help-centre content.

### 2.3 Key User Journeys

- **UJ-1. Priya compares her spending by category.**
  Priya, a customer in the demo dataset, has the chat open and asks "How much did I spend by category last month?". The reply streams in as usual: a short summary of her top merchant categories. When the text finishes, a Bar Chart appears under it with one bar per Category, amounts in USD, and a title naming the month. Her five Categories qualify (3–8). She sees at once that dining is out of line.

- **UJ-2. Priya follows a trend.**
  Next she asks "How has my monthly spending changed this year?". The text gives the headline movement, and a Line Chart of 11 monthly Time Points appears under it, in time order. Then she asks "Show my balance trend this year" and gets a Line Chart of her end-of-month balance. **Edge case:** if she asks for daily spending across the whole month (about 30 Time Points), she gets the Text Reply and no Chart.

- **UJ-3. Priya asks something that shouldn't be charted.**
  She asks "What's my current balance?" (one value), then "Show my last 10 transactions" (a Raw Transaction List), then "How do I reset my PIN?" (help-centre). Each gets its normal Text Reply and no Chart. None of these Text Replies mentions a chart, because she didn't make an Explicit Chart Request.

- **UJ-4. Priya asks for a chart that the rules don't allow.**
  She asks "Give me a pie chart of my spending by merchant". She has 14 merchants. She gets the Text Reply, a one-line note that no chart was shown and why, and no Chart. Her question is answered, not sent to the fallback flow as off-topic.

## 3. Glossary

- **Reply** — Everything FinTell returns for one request: the Text Reply plus at most one Chart.
- **Text Reply** — The chat text produced by the existing response flow. It is always present and does not depend on whether a Chart is built, with one exception: the FR-5 note, which depends on the Chart Decision.
- **Chart** — A Bar Chart or Line Chart shown under the Text Reply, built from the Result. There is at most one per Reply. It is not saved with chat history in v1.
- **Bar Chart** — A Chart that compares values across Categories.
- **Line Chart** — A Chart that shows values across Time Points, in time order.
- **Category** — One distinct grouping value in a Result, such as a merchant category or a merchant brand. Each Category is one Data Point on a Bar Chart.
- **Time Point** — One distinct period in a Result (day, week or month, whatever the Result is grouped by). Each Time Point is one Data Point on a Line Chart.
- **Data Point** — One plotted value: a Category or a Time Point together with its amount.
- **SQL Sub-query** — One transaction question that the supervisor split out of the user's message for text-to-SQL. FinTell answers all of a request's SQL Sub-queries with one combined SQL statement.
- **Result** — The rows returned by the request's one SQL statement. There is at most one Result per request.
- **Raw Transaction List** — A Result in which each row is one transaction rather than an aggregate (for example "my last 10 payments"), including any Result that the row cap has truncated.
- **Explicit Chart Request** — A user message that asks for a visual in so many words: "chart", "graph", "plot", "visualise"/"visualize", or a named chart type ("bar", "line", "pie"…). "Show my…" or "trend" on its own is not an Explicit Chart Request.
- **Chart Eligibility Rules** — The fixed rules in §4.1 that decide whether a Chart is shown and which type.
- **Chart Decision** — The outcome of applying the Chart Eligibility Rules to one request: a chart type (bar or line), or "no chart" with a Skip Reason.
- **Skip Reason** — A fixed label explaining a "no chart" Chart Decision. The full set is the FR-3 table.
- **Chart Path** — All the work that produces the Chart Decision and the Chart, from reading the Result to drawing the Chart in the browser.

## 4. Features

### 4.1 Chart Eligibility

**Description:** For each request, FinTell makes one Chart Decision by applying the Chart Eligibility Rules to the Result. The rules are deterministic in outcome: the same Result shape always gives the same Chart Decision. When a Result's shape is ambiguous, the rules choose "no chart". Realizes UJ-1 to UJ-4.

**Functional Requirements:**

#### FR-1: Bar Chart for comparisons across Categories

FinTell shows a Bar Chart when the Result compares an amount across **3 to 8 Categories, inclusive**. Realizes UJ-1.

**Consequences (testable):**
- A Result with 3 Categories or 8 Categories gets a Bar Chart.
- A Result with 2 Categories or 9 Categories gets no Chart.
- Above 8 Categories, FinTell does not truncate to a top-N, add an "Other" bar, or regroup.

#### FR-2: Line Chart for trends over Time Points

FinTell shows a Line Chart when the Result gives an amount across **3 to 12 Time Points, inclusive**. Realizes UJ-2.

**Consequences (testable):**
- 12 monthly Time Points get a Line Chart. 13 get no Chart.
- Daily rows across a whole month (more than 12 Time Points) get no Chart and are not regrouped into weeks.
- Time Points are plotted in time order, whatever order the rows came back in.
- Only the Time Points present in the Result are counted and plotted. Missing periods are not filled in.

#### FR-3: No Chart in the excluded cases

FinTell shows no Chart when any case below applies. When more than one applies, the Skip Reason of the **first matching row in table order** is reported. Realizes UJ-3.

| Order | Case | Skip Reason |
| --- | --- | --- |
| 1 | The Chart Path failed (see FR-13) | `chart_error` |
| 2 | A fallback was signalled on any path: the query was off-topic or blocked by the guardrail, or any stage reported a failure | `fallback` |
| 3 | The request had no SQL Sub-query (help-only) | `no_transaction_data` |
| 4 | The Result is empty | `empty_result` |
| 5 | The Result is a Raw Transaction List, even if it has 3–12 dated rows | `raw_list` |
| 6 | The Result is not a single-series Category comparison or Time Point trend: its shape is ambiguous, it is grouped two ways (FR-7), it mixes directions (FR-8), or a combined Result from several SQL Sub-queries doesn't fit one shape (FR-6) | `unsuitable_shape` |
| 7 | Fewer than 3 Data Points (a single total, two values) | `too_few_points` |
| 8 | More than 8 Categories | `too_many_categories` |
| 9 | More than 12 Time Points | `too_many_time_points` |

**Consequences (testable):**
- Each case in the table gives no Chart and the stated Skip Reason. Where cases overlap, the reported Skip Reason follows the table order (for example, an empty Result after a fallback reports `fallback`).
- In every case, the Text Reply comes from the unchanged response flow using the same Result. The only added text is the FR-5 note.
- A supervisor error that ends with no Reply at all is a known baseline defect (spine AD-7). It is exempt from this table and from FR-14 until that defect is fixed.

#### FR-4: Bar Chart and Line Chart only

FinTell never shows any chart type other than a Bar Chart or Line Chart. Realizes UJ-4.

**Consequences (testable):**
- A request for a pie, scatter, area or any other type never produces that type.
- If the Result otherwise qualifies for a Bar Chart or Line Chart, FinTell shows the qualifying type in its place. For example, "pie chart of spending by category" with 5 Categories gets a Bar Chart. The substitution is not mentioned in the Text Reply.

#### FR-5: An Explicit Chart Request doesn't override the rules

When the user makes an Explicit Chart Request, the Chart Eligibility Rules still decide. Realizes UJ-4.

**Consequences (testable):**
- "Chart my spending by merchant" with 14 merchants gives no Chart.
- An Explicit Chart Request about the user's transactions is treated as an in-scope transaction question. Mentioning a chart never causes it to be sent to fallback as off-topic.
- When the user made an Explicit Chart Request and the Chart Decision is "no chart", the Text Reply adds one short, plain line saying no chart was shown and why (for example "There are too many merchants to chart clearly").
- Without an Explicit Chart Request, a "no chart" Chart Decision adds nothing to the Text Reply.
- If the Skip Reason is `chart_error`, the note is left out. A failure is never explained to the user.
- The note is part of the Text Reply and is saved with chat history like the rest of it.

**Notes:**
- **[NOTE FOR PM] The note links the Text Reply to the Chart Decision.** This is the one place the Text Reply depends on the Chart Path. To keep FR-10 (time to first token unchanged) and FR-13 (failure never touches the text), the Chart Decision must be made **before** the Text Reply starts and must add no noticeable latency. In practice, that means a code-only decision. See the addendum, "Where the chart step sits".

#### FR-6: One Chart per Reply, from the one Result

A Reply has at most one Chart, built from the request's one Result. Realizes UJ-1, UJ-2.

**Consequences (testable):**
- When a request has several SQL Sub-queries, FinTell still produces one combined Result (baseline behaviour). That Result is charted only if, as a whole, it meets FR-7 and either FR-1 or FR-2. Otherwise the Skip Reason is `unsuitable_shape`.
- In a mixed transaction-and-help question, only the transaction part can be charted. Help-centre content is never charted.

#### FR-7: Single series only

A Chart shows a single series: one amount per Category, or one amount per Time Point. Results grouped two ways, such as spending per Category per month, get no Chart (`unsuitable_shape`). Multi-series Charts are not in v1.

**Consequences (testable):**
- A Result grouped by both merchant category and month gives no Chart.

#### FR-15: Balance trend gets a Line Chart

When the user asks how their balance has changed over a period ("show my balance trend this year"), FinTell answers with the **end-of-period balance for each Time Point** and shows a Line Chart, within FR-2's 3–12 limit. This changes the text-to-SQL behaviour for balance-trend questions, not only the presentation. Realizes UJ-2.

**Consequences (testable):**
- "Show my balance trend this year" with 11 months of data gets a Line Chart of 11 end-of-month balances.
- The value plotted for each Time Point is the `balance` of the last transaction in that period (`balance` is the running balance after each row). When several transactions share the last date, architecture defines the tie-break, for example by `transaction_id`.
- When a trend is asked for, the answer is never one balance row per transaction. That would be a Raw Transaction List and get no Chart.
- A balance trend with more than 12 Time Points (for example daily across a month) gets no Chart, as FR-2 requires.
- A balance trend uses only the asking customer's own transactions. Asking the same question for two customers gives each their own series and never mixes rows.

**Notes:**
- **[NOTE FOR PM] Clear the SQL cache before the demo.** FR-15 means changing the text-to-SQL prompt. Cached SQL in `query_cache` never expires, and the Redis `qcache:*` keys last 24 hours (AGENTS.md). So balance-trend questions keep getting the old per-transaction SQL until both are cleared.

### 4.2 Chart Presentation

**Description:** When a Chart is due, it appears under the Text Reply in the chat, looks like part of the FinTell UI, and is readable without explanation. Realizes UJ-1, UJ-2.

**Functional Requirements:**

#### FR-8: Chart content

Every Chart has a title, labelled axes and USD amounts. Realizes UJ-1, UJ-2.

**Consequences (testable):**
- The title names what is measured and the period (for example "Spending by category — October 2026").
- The value axis is labelled in USD. The other axis is labelled with the Category grouping or the Time Point period.
- A Chart shows one direction only, either spending (money out) or income (money in), unless the question is about net cash flow or balance. `amount_usd` is always positive; direction comes from `transaction_type`.
- A Result that mixes spending and income amounts without being a net or balance figure gets no Chart (`unsuitable_shape`).
- Net and balance values may be negative and are plotted as they are.

#### FR-9: Placement and theme

The Chart is shown directly under the Text Reply it belongs to, and follows the UI's current light or dark theme.

**Consequences (testable):**
- Switching theme leaves the Chart readable (axes, labels and the plotted series stay visible).
- The Chart fits the chat column at 1280 px browser width without horizontal scrolling. Phone-width layout is not a v1 target.

#### FR-10: Streaming behaviour

On the streaming endpoint, the Text Reply streams as it does today, and the Chart appears after the text has finished.

**Consequences (testable):**
- The median time to the first streamed token changes by no more than 200 ms.
- The Chart arrives only with the stream's final event, never part-way through the text. No new stream event type is added.
- No Chart Path output is streamed as Text Reply tokens. The FR-5 note is written by the response flow, not by the Chart Path.

#### FR-11: Endpoint parity

`/process` and `/process-stream` return the same Chart (or the same absence of a Chart) for the same request (spine AD-13).

**Consequences (testable):**
- For each question in the evaluation dataset, both endpoints give the same Chart Decision.
- With no Chart, the chart field is present in both endpoints' responses with the same empty value. Architecture fixes the exact value.

#### FR-12: Not saved with history

In v1, Charts are not saved. Reloading a past conversation shows the Text Replies only.

**Consequences (testable):**
- After reloading the page, earlier Replies show no Chart and no error or placeholder.

### 4.3 Reliability and Visibility

**Description:** The Chart Path can never cost the user their answer, and a presenter or developer can see why each Chart Decision was made.

**Functional Requirements:**

#### FR-13: The Chart Path never breaks the Reply

Any failure on the Chart Path results in no Chart, with Skip Reason `chart_error`. The Text Reply is unaffected.

**Consequences (testable):**
- If an error is injected on the Chart Path, the Reply has the normal Text Reply, no Chart, and no fallback or error text from the Chart Path.
- A Chart Path failure never sends the request to the fallback flow and never adds to the fallback message. (Architecture must settle the spine's deferred item on who may write `fallback_response`.)
- Malformed or partial chart data never crashes the UI. The browser shows the Text Reply with no Chart.
- If the chart library fails to load in the browser, the Text Reply still shows, with no Chart and no error that breaks the page.

#### FR-14: Chart Decision is traceable

The Chart Decision and its Skip Reason are visible in the agent trace that FinTell already returns, and in the existing UI trace panel, so a presenter can explain why there was no Chart. Any LLM use on the Chart Path records token, latency and cost metrics the same way as other stages (spine AD-12, AD-16). The Chart Decision is shown in the trace only and is not saved.

**Consequences (testable):**
- Every Reply's trace includes the Chart Decision: chart type, or "no chart" plus its Skip Reason.

## 5. Non-Goals (Explicit)

- No chart types other than Bar Chart and Line Chart: no pie, scatter, area, stacked, multi-series or combo charts.
- No top-N truncation, "Other" buckets, filling in missing periods, or automatic regrouping to make a Result chartable.
- No charts of help-centre content.
- No interactive analysis: no drill-down, filtering, chart editing, or user-chosen axes. Default hover tooltips are fine but not required.
- No exporting or downloading charts, and no dashboards.
- No change to how Text Replies are produced, with two deliberate exceptions: the FR-5 note, and FR-15's end-of-period answer for balance-trend questions.
- No change to the `transactions` schema or data.
- No extra queries on the Chart Path. It works only from the Result.

## 6. MVP Scope

### 6.1 In Scope

- Chart Eligibility Rules (FR-1 to FR-7), with fixed Skip Reasons in a set order.
- Balance trend answered as end-of-period balance per Time Point, with a Line Chart (FR-15). This is a text-to-SQL behaviour change.
- Bar Chart and Line Chart rendering under the Text Reply, matching the theme (FR-8, FR-9).
- Streaming behaviour, endpoint parity, and no chart persistence (FR-10, FR-11, FR-12).
- A Chart Path that fails safely, and trace visibility (FR-13, FR-14).
- An evaluation dataset of about 30 labelled demo questions in `tests/data/` (see §7).
- Making sure the unused visualization code does not conflict with the new feature. Whether to reuse or remove it is decided in architecture.

### 6.2 Out of Scope for MVP

- Persisting Charts with chat history (see FR-12). Revisit if the demo needs replay of past conversations.
- Multi-series Charts (excluded by FR-7). Revisit when "category by month" questions are wanted on stage.
- Running each SQL Sub-query separately so each can be charted (see FR-6). That would change the SQL subgraph.
- Mobile or phone-width layout.

## 7. Success Metrics

Measured by running the evaluation dataset against `/process`, plus a parity run against `/process-stream`. The dataset has about 30 labelled questions covering FR-1 to FR-7, FR-15, every Skip Reason, and each UJ-4 phrasing.

The dataset is run from a cleared SQL cache, then run again with the cache warm. The Chart Decision must be the same on both runs.

**Primary**
- **SM-1: No-chart correctness** — the share of questions labelled "no chart" that get no Chart, with the FR-5 note present exactly when there is an Explicit Chart Request. Target **100%**. Validates FR-3, FR-5, FR-7.
- **SM-2: Chart correctness** — the share of questions labelled "chart" that get a Chart of the labelled type, including pie-to-bar substitution. Target **≥ 90%**. Validates FR-1, FR-2, FR-4, FR-6, FR-15.

**Secondary**
- **SM-3: Added latency** — median end-to-end time with the feature on versus off, on the same questions. Target: **≤ 2 s** added for charted Replies, and **≤ 200 ms** change for un-charted Replies and for time to first token. Validates FR-10.
- **SM-4: Parity** — the Chart Decision matches across both endpoints for **100%** of the dataset, excluding the baseline paths where the endpoints already differ (spine AD-7/AD-13 as-is). Validates FR-11.
- **SM-5: Zero lost replies** — no question in the dataset, including injected Chart Path failures, loses or changes its Text Reply because of the Chart Path. Validates FR-13.
- **SM-6: Skip Reason match (diagnostic)** — the share of "no chart" questions whose traced Skip Reason matches the label. Target **≥ 90%**. Validates FR-3, FR-14.

FR-8, FR-9 and FR-12 are verified by their own testable consequences rather than by a metric.

**Counter-metrics (do not optimize)**
- **SM-C1: Chart rate** — the share of Replies that have a Chart. More Charts is not better. Report it on the dataset, and flag it if it exceeds the share labelled "chart". Counterbalances SM-2.
- **SM-C2: Text Reply drift** — Text Replies must not get shorter or weaker because a Chart "carries" the answer. Compare Text Reply length on the dataset with the feature on and off, and spot-check by hand. Counterbalances SM-2.

## 8. Open Questions

1. ~~**Balance trend in the demo.**~~ Resolved: yes, see FR-15.
2. **Time Point granularity.** Is a Time Point whatever period the SQL grouped by (day, week, month), or should the rules apply only to months? Default in use: whatever the Result is grouped by. The dataset labels follow this default. Owner: architecture. Revisit if a demo question lands on the wrong side of the 12-point limit.
3. **Missing periods.** Default in use: plot only the Time Points present, and don't fill gaps (FR-2). Should a missing month be drawn as zero instead? Owner: UX. Revisit at UX design.

## 9. Assumptions Index

None open. All five assumptions in the draft (FR-4, FR-7, FR-9, FR-13, FR-14) were confirmed on 2026-11-22 and are now stated as requirements.
