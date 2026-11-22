# Evaluation plan — Transaction Charts

This plan defines how CAP-12 measures the feature. The run mechanics (session isolation, rate limit, flags, which trace fields to read) are set by spine AD-25 and AD-22, and are not repeated here.

## Metrics

They are measured against `/process`, with a parity pass against `/process-stream`.

| ID | Metric | Target | Validates |
| --- | --- | --- | --- |
| SM-1 | No-chart correctness: the share of questions labelled "no chart" that get no Chart, with the FR-5 note present exactly when there is an Explicit Chart Request | **100%** | CAP-4, CAP-6 |
| SM-2 | Chart correctness: the share of questions labelled "chart" that get a Chart of the labelled type, including pie-to-bar substitution | **≥ 90%** | CAP-1, CAP-2, CAP-3, CAP-5 |
| SM-3 | Added latency: median end-to-end time with the feature on versus off, on the same questions | **≤ 2 s** added for charted Replies; **≤ 200 ms** change for un-charted Replies and for time to first token | CAP-8 |
| SM-4 | Parity: the Chart Decision matches across both endpoints, excluding the baseline paths where the endpoints already differ (AD-7, AD-13 as-is) | **100%** | CAP-8 |
| SM-5 | Zero lost replies: no question, including those in the fault-injection pass, loses or changes its Text Reply because of the Chart Path | **0** affected | CAP-10 |
| SM-6 | Skip Reason match (diagnostic): the share of "no chart" questions whose traced Skip Reason matches the label | **≥ 90%** | CAP-4, CAP-11 |

**Counter-metrics.** Do not optimise these; report them.
- **SM-C1, chart rate:** the share of Replies that have a Chart. Flag it if it exceeds the share of questions labelled "chart". It counterbalances SM-2.
- **SM-C2, Text Reply drift:** Text Reply length with the feature on versus off, plus a spot-check by hand. The text must not get shorter or weaker because a Chart "carries" the answer. It counterbalances SM-2.

CAP-7 and CAP-9 are verified by their own success criteria, not by a metric.

## Dataset

There are about 30 labelled questions in `tests/data/`, stored as data only.

**Columns:** `question`, `customer_id`, `expected_chart` (`bar` / `line` / `none`), `expected_skip_reason`, `explicit_request` (bool), `expect_note` (bool).

Labels follow the AD-20 decision table order.

**Must cover:**

- every Skip Reason, including an injected `chart_error`
- both sides of each limit: 2/3 and 8/9 Categories, 12/13 Time Points
- a Result with more than 20 periods
- a mixed SQL-and-help question
- a combined question with several SQL Sub-queries
- a mixed-direction Result
- each UJ-4 phrasing and pie-to-bar substitution
- the FR-15 balance trend for two different customers
- positive and negative examples of the Explicit Chart Request pattern, including inside-word cases (`Chartered`, `online`) and negations

## Passes

1. Cold, after `DELETE FROM query_cache` and deleting the Redis `qcache:*` keys.
2. Warm. The Chart Decision must equal the cold pass, excluding rows whose trace has a `sql_correction` entry.
3. Parity on `/process-stream` (SM-4).
4. Fault injection, with `CHART_FAULT_INJECTION=true` (SM-5).
5. Feature off, with `CHART_FEATURE_ENABLED=false`. This is the SM-3 and SM-C2 baseline.

Labels that depend on dates (for example "11 months this year") go stale after a data `shift`. Re-label them after each shift.

## Reference scenarios (from the PRD user journeys)

These are the demo questions, and they must be in the dataset. Priya is a customer in the seeded data.

| Journey | Question | Expected Chart | Skip Reason | Note |
| --- | --- | --- | --- | --- |
| UJ-1 | How much did I spend by category last month? (5 Categories) | bar, with the title naming the month | — | no |
| UJ-2 | How has my monthly spending changed this year? (11 months) | line, 11 points in time order | — | no |
| UJ-2 | Show my balance trend this year | line, end-of-month balances | — | no |
| UJ-2 | Daily spending across the whole month (~30 days) | none | `too_many_time_points` | no |
| UJ-3 | What's my current balance? | none | `too_few_points` | no |
| UJ-3 | Show my last 10 transactions | none | `raw_list` | no |
| UJ-3 | How do I reset my PIN? | none | `no_transaction_data` | no |
| UJ-4 | Give me a pie chart of my spending by merchant (14 merchants) | none; answered, not sent to fallback | `too_many_categories` | yes |
| UJ-4 | Chart my spending by merchant (14 merchants) | none; answered, not sent to fallback | `too_many_categories` | yes |
| UJ-4 | Pie chart of spending by category (5 Categories) | bar, with no mention of the substitution | — | no |
| FR-3 | Income vs spending | none | `too_few_points` | no |
