# Visualization Agent Overview

This document describes how the application decides when to create a chart, prepares chart data, and avoids showing misleading or overly crowded visualizations.

## End-to-end flow

1. **Recognize an explicit chart request.** Phrases such as “create a chart,” “show a graph,” “make a viz,” and “plot my spending” are treated as requests to create a visualization. The intent helper is shared by the SQL-generation, SQL-execution, and visualization stages so that they use the same explicit-request rule.
2. **Generate a chart-oriented query.** For explicit chart requests, the Text-to-SQL agent is prompted to return grouped SQL summaries rather than individual transactions. It is guided to aggregate before applying a row limit, use a suitable time or category grouping, and choose a measure appropriate to the request.
3. **Execute with the appropriate result cap.** A grouped aggregate query associated with an explicit chart request can return up to 500 summary rows. Other query results—including raw transaction rows—retain the 20-row cap. SQL execution fetches one extra row beyond the applicable cap to detect truncation.
4. **Validate and prepare the chart.** The visualization agent checks the result structure, selected fields, numeric values, date/category values, and currency consistency before rendering.
5. **Render the chart or skip it.** Bar, line, and pie charts are supported. If the results are incomplete or invalid, the chart is skipped instead of presenting a partial or malformed visualization.

## Explicit requests and response behavior

The intent detector recognizes common action phrases and spelling variants, including “viz,” “vizualization,” “visualization,” and “visualisation.” A question that merely discusses charts does not automatically count as a creation request.

When an explicit chart request has usable data and a chart renders successfully, the response is chart-only: the normal natural-language response is skipped. If the chart cannot be created—for example, because no suitable numeric data is available—the chart-only path is not enabled, so the normal response flow can explain or answer the request.

An explicit pie-chart request is honored when the results support a pie chart. If the values are unsuitable, the chart is not rendered and the response tells the user to try a line or bar chart instead.

For other questions, the visualization agent can still choose to create a chart, but it does not force one. The model receives the question and column names/types, not transaction sample values.

## Aggregation and result limits

For explicit visualization requests, SQL generation is instructed to produce compact grouped summaries—for example, spending by date, month, merchant, or category—instead of returning the underlying transactions. The SQL result executor recognizes an explicit chart request as eligible for the larger result cap only when the query includes both `GROUP BY` and an aggregate expression.

| Result type | Maximum returned rows | Behavior when more results exist |
|---|---:|---|
| Ordinary results, including transaction details | 20 | Results are capped, and truncation is flagged. |
| Grouped aggregate results for an explicit chart request | 500 | Results are capped, and truncation is flagged. |

The extra probe row makes the cap detectable. If truncation is flagged, the visualization agent refuses to chart the incomplete result. This avoids implying that a chart covers the complete result set.

The larger cap does not apply to a chart request that returns raw, ungrouped rows. Those results remain subject to the 20-row limit and may be truncated.

## Measure and time handling

For explicit chart construction, the current field-name-based aggregation rules are:

- **Amounts, spending, counts, and totals:** sum values within each displayed group.
- **Rates, ratios, percentages, averages:** use the arithmetic mean.
- **Balances:** use the latest dated value per category; balances are not added together.

When possible, the measure is selected to match terms in the user’s request, such as balance, rate, count, or spending. These rules rely on result field names and available date columns; they are heuristics rather than schema-level measure metadata.

Date grouping preserves year and month (and day when the query provides it), so the same month in different years is not combined accidentally. Line-series points are chronologically ordered and long series are downsampled while retaining endpoints and selecting points intended to preserve peaks and dips.

## Pie charts

Pie charts are used for categorical part-to-whole composition, not time trends or arbitrary comparisons. Values must be non-negative, have a positive total, and use an additive measure such as spending, amount, or count. Balances, rates, ratios, percentages, and averages are not treated as pie-compatible metrics. At least two categories are required.

Pie charts show the five largest categories by value. Any remaining categories are summed into **Other**, yielding at most six slices. The chart includes labels and percentages. When a user explicitly requests a pie chart for unsuitable values, the application explains why and recommends trying a line or bar chart.

## Chart readability and validation

- **Bar charts:** show up to eight categories. When there are more, the largest seven are retained and the remaining categories are grouped into **Other**.
- **Line charts:** display at most twelve points; longer series are sampled and titled accordingly.
- **Pie charts:** show the top five categories and combine the remainder into **Other**.
- **Currencies:** currency may be inferred from a result field suffix or explicit currency values. Mixed or conflicting currencies are rejected rather than combined into a misleading scale.
- **Data checks:** charting is skipped for inconsistent row schemas, absent fields, missing x values, nonnumeric/non-finite measures, duplicate categories, incompatible chart/time axes, or incomplete results.
- **Chart types:** bar, line, and pie charts are supported.

## Relevant implementation

- [`src/utils/chart_intent.py`](./src/utils/chart_intent.py) — shared explicit chart-request detection.
- [`src/agents/text2sql_generator.py`](./src/agents/text2sql_generator.py) — chart-specific SQL-generation guidance and separate chart-query cache keys.
- [`src/agents/sql_execution.py`](./src/agents/sql_execution.py) — grouped-chart eligibility, result caps, and truncation detection.
- [`src/agents/visualization_agent.py`](./src/agents/visualization_agent.py) — chart validation, aggregation, downsampling, and Plotly rendering.
- [`src/agents/response_generator.py`](./src/agents/response_generator.py) — suppresses text generation after a successful explicit chart-only response.
- [`tests/test_visualization_agent.py`](./tests/test_visualization_agent.py) — focused visualization and SQL-result regression tests.

Run the focused tests from the repository root:

```powershell
python -m unittest tests.test_visualization_agent -v
```
