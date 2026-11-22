# Glossary — Transaction Charts

These terms are used exactly, in SPEC.md, in its companions and in code comments. Code-level names (`chart_response`, `ChartSpec`, `SkipReason`, the SQL column convention) are defined in the spine (AD-19, AD-20).

- **Reply** — Everything FinTell returns for one request: the Text Reply plus at most one Chart.
- **Text Reply** — The chat text produced by the existing response flow (`response_generator`). It is always present and does not depend on the Chart Path, with one exception: the FR-5 note, which depends on the Chart Decision.
- **Chart** — A Bar Chart or Line Chart shown under the Text Reply, built from the Result. There is at most one per Reply. It is not saved in v1.
- **Bar Chart** — A Chart that compares values across Categories.
- **Line Chart** — A Chart that shows values across Time Points, in time order.
- **Category** — One distinct grouping value in a Result, such as a merchant category or a merchant brand. Each Category is one Data Point on a Bar Chart.
- **Time Point** — One distinct period in a Result, at whatever grain the SQL grouped by: year, month, ISO week or day (AD-20). Each Time Point is one Data Point on a Line Chart.
- **Data Point** — One plotted value: a Category or a Time Point together with its amount.
- **SQL Sub-query** — One transaction question that the supervisor split out of the user's message (`banksql_queries`). FinTell answers all of a request's SQL Sub-queries with one combined SQL statement.
- **Result** — The rows returned by the request's one SQL statement (`database_query_results`). There is at most one Result per request.
- **Raw Transaction List** — A Result in which each row is one transaction rather than an aggregate (for example "my last 10 payments"). A Result that the row cap truncated and that doesn't follow the AD-20 column convention also counts as one. A truncated comparison or trend reports `too_many_categories` or `too_many_time_points` instead.
- **Explicit Chart Request** — A user message that asks for a visual in so many words: "chart", "graph", "plot" or "visualise"/"visualize", or a named chart type ("bar", "line", "pie"…). "Show my…" or "trend" on its own is not one. The exact pattern is defined in AD-21.
- **Chart Eligibility Rules** — The fixed rules that decide whether a Chart is shown, and of which type: CAP-1 to CAP-5, applied as the AD-20 decision table.
- **Chart Decision** — The outcome of applying the Chart Eligibility Rules to one request: a chart type (`bar` or `line`), or "no chart" with a Skip Reason.
- **Skip Reason** — A fixed label explaining a "no chart" Chart Decision. There are nine: `chart_error`, `fallback`, `no_transaction_data`, `empty_result`, `raw_list`, `too_few_points`, `unsuitable_shape`, `too_many_categories` and `too_many_time_points`. When several apply, the first matching row of the AD-20 decision table wins.
- **Chart Path** — All the work that produces the Chart Decision and the Chart, from reading the Result to drawing the Chart in the browser.
- **FR-5 note** — The one short, fixed line appended to the Text Reply when there was an Explicit Chart Request and the Chart Decision is "no chart" for any reason other than `chart_error`.
