import math
import re
import time
from datetime import datetime
from typing import Any
from logging import Logger

import plotly.graph_objects as go
import plotly.io as pio

from app_models import Graph_State, VisualizationDecision, VisualizationResponse
from utils import agent_utils
from utils.chart_intent import (
    is_explicit_chart_request,
    is_explicit_pie_request,
)

# A chart is only worth showing on the (narrow, mobile) UI when it has enough points to
# compare/trend, but not so many that it turns into clutter.
#   - MIN_CHART_ROWS: minimum for bar/line charts; pies need at least two categories.
#   - MAX_BAR_CATEGORIES: cap on x-axis values for a bar chart (kept tight for mobile width).
#   - MAX_LINE_POINTS: rendered point cap; longer series are downsampled with peak preservation.
MIN_CHART_ROWS = 3
MAX_BAR_CATEGORIES = 8
MAX_LINE_POINTS = 12
MAX_PIE_CATEGORIES = 5

TIME_FIELDS = {"_chart_period", "date", "month", "transaction_date", "year_month"}
CURRENCY_FIELDS = {"currency", "currency_code", "transaction_currency"}
CURRENCY_SUFFIXES = {"aud", "cad", "chf", "cny", "eur", "gbp", "inr", "jpy", "nzd", "usd"}
CURRENCY_SYMBOLS = {"£": "GBP", "€": "EUR"}
def _skip_viz(reasoning: str, decision: VisualizationDecision | None = None) -> VisualizationResponse:
    """Build a 'no chart' response, preserving any title/fields the LLM already chose."""
    return VisualizationResponse(
        decision=VisualizationDecision(
            should_visualize=False,
            chart_type="none",
            chart_title=decision.chart_title if decision else "",
            x_field=decision.x_field if decision else None,
            y_field=decision.y_field if decision else None,
            reasoning=reasoning,
        ),
        plotly_json=None,
    )


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _currency_info(
    rows: list[dict[str, Any]], y_field: str
) -> tuple[str | None, bool]:
    explicit_fields = CURRENCY_FIELDS.intersection(
        {str(key).lower() for row in rows for key in row}
    )
    suffix_match = re.search(r"_([a-z]{3})$", y_field.lower())
    suffix_code = suffix_match.group(1).upper() if suffix_match else None
    if suffix_code not in {code.upper() for code in CURRENCY_SUFFIXES}:
        suffix_code = None

    if explicit_fields:
        codes: set[str] = set()
        for row in rows:
            values = [
                row[key]
                for key in row
                if str(key).lower() in explicit_fields
            ]
            if not values or any(value is None for value in values):
                return None, True
            for value in values:
                code = str(value).strip().upper()
                code = CURRENCY_SYMBOLS.get(code, code)
                if (len(code) != 3 or not code.isalpha()) and code not in {"$", "¥"}:
                    return None, True
                codes.add(code)
        if len(codes) != 1:
            return None, True
        currency = next(iter(codes))
        if currency == "$" and suffix_code:
            currency = suffix_code
        elif currency == "¥" and suffix_code in {"CNY", "JPY"}:
            currency = suffix_code
        if suffix_code and suffix_code != currency:
            return None, True
        return currency, False

    return suffix_code, False


def _time_sort_key(value: Any) -> tuple[int, Any]:
    numeric = _numeric_value(value)
    if numeric is not None:
        return (0, numeric)

    text = str(value).strip()
    try:
        return (1, datetime.fromisoformat(text).timestamp())
    except (ValueError, OverflowError):
        pass
    try:
        return (1, datetime.strptime(text, "%Y-%m").timestamp())
    except ValueError:
        return (2, text.casefold())


def _aggregation_mode(field: str) -> str:
    name = field.lower()
    if "balance" in name:
        return "latest"
    if any(term in name for term in ("rate", "ratio", "percent", "average", "avg")):
        return "mean"
    if any(term in name for term in ("amount", "spent", "spend", "count", "total")):
        return "sum"
    return "mean"


def _collapse_bar_categories(
    rows: list[dict[str, Any]],
    x_field: str,
    y_field: str,
) -> tuple[list[dict[str, Any]], int]:
    if len(rows) <= MAX_BAR_CATEGORIES:
        return rows, 0

    ordered = sorted(rows, key=lambda row: float(row[y_field]), reverse=True)
    visible_count = MAX_BAR_CATEGORIES - 1
    kept = ordered[:visible_count]
    omitted = ordered[visible_count:]
    mode = _aggregation_mode(y_field)
    omitted_values = [float(row[y_field]) for row in omitted]
    other_value = (
        sum(omitted_values) / len(omitted_values)
        if mode == "mean"
        else sum(omitted_values)
    )
    other_row = {x_field: "Other", y_field: other_value}
    for currency_field in CURRENCY_FIELDS:
        if currency_field in omitted[0]:
            other_row[currency_field] = omitted[0][currency_field]
    return [*kept, other_row], len(omitted)


def _collapse_pie_categories(
    rows: list[dict[str, Any]],
    x_field: str,
    y_field: str,
) -> tuple[list[dict[str, Any]], int]:
    if len(rows) <= MAX_PIE_CATEGORIES:
        return rows, 0

    ordered = sorted(rows, key=lambda row: float(row[y_field]), reverse=True)
    kept = ordered[:MAX_PIE_CATEGORIES]
    omitted = ordered[MAX_PIE_CATEGORIES:]
    other_row = {
        x_field: "Other",
        y_field: sum(float(row[y_field]) for row in omitted),
    }
    for currency_field in CURRENCY_FIELDS:
        if currency_field in omitted[0]:
            other_row[currency_field] = omitted[0][currency_field]
    return [*kept, other_row], len(omitted)


def _pie_alternative_message(reason: str) -> str:
    return (
        f"Pie chart not supported for these values: {reason} "
        "Please try a line or bar chart instead."
    )


def _validate_chart_data(
    decision: VisualizationDecision,
    rows: list[dict[str, Any]],
    *,
    explicit_request: bool = False,
) -> str | None:
    if decision.chart_type not in {"bar", "line", "pie"}:
        return "Only bar, line, and pie charts can be rendered."
    if not decision.should_visualize:
        return "The chart decision did not request a visualization."
    if not rows:
        return "No rows are available to chart."
    if not decision.x_field or not decision.y_field:
        return "The chart decision must specify both x and y fields."
    if decision.x_field == decision.y_field:
        return "The x and y fields must be different."

    available_fields = set(rows[0])
    if any(set(row) != available_fields for row in rows):
        return "Result rows do not share a consistent set of fields."
    if decision.x_field not in available_fields or decision.y_field not in available_fields:
        return "The selected chart fields are not present in the query results."
    if any(row[decision.x_field] is None for row in rows):
        return "The selected x field contains missing values."
    if any(_numeric_value(row[decision.y_field]) is None for row in rows):
        return "The selected y field contains non-numeric or non-finite values."

    x_values = [str(row[decision.x_field]).strip() for row in rows]
    if any(not value for value in x_values):
        return "The selected x field contains empty values."
    if len(set(x_values)) != len(x_values):
        return "The selected x field contains duplicate categories."
    minimum_points = (
        2 if decision.chart_type == "pie"
        else 1 if explicit_request
        else MIN_CHART_ROWS
    )
    if len(x_values) < minimum_points:
        return f"At least {minimum_points} distinct values are needed for a chart."

    x_name = decision.x_field.lower()
    if decision.chart_type == "line" and x_name not in TIME_FIELDS:
        return "Line charts require a time field on the x axis."
    if decision.chart_type == "bar" and x_name in TIME_FIELDS and not explicit_request:
        return "Time-based x fields should use a line chart."
    if decision.chart_type == "pie":
        if x_name in TIME_FIELDS:
            return "Pie charts require categorical values, not a time series."
        measure_name = decision.y_field.lower()
        if (
            _aggregation_mode(measure_name) != "sum"
            or any(
                term in measure_name
                for term in ("balance", "rate", "ratio", "percent", "average", "avg")
            )
        ):
            return (
                "Pie charts require an additive measure such as spending, amount, "
                "or count."
            )
        values = [float(row[decision.y_field]) for row in rows]
        if any(value < 0 for value in values):
            return "Pie charts cannot represent negative values."
        if sum(values) <= 0:
            return "Pie charts require a positive total."
        if len(x_values) < 2:
            return "Pie charts require at least two categories."

    _, mixed_currency = _currency_info(rows, decision.y_field)
    if mixed_currency:
        return "Results contain mixed or conflicting currencies."
    return None


def _explicit_chart_requested(state: Graph_State) -> bool:
    original_query = (
        state.input_request.user_query if state.input_request is not None else ""
    )
    classified_query = " ".join(state.banksql_queries or [])
    return (
        is_explicit_chart_request(original_query)
        or is_explicit_chart_request(classified_query)
    )


def _explicit_pie_requested(state: Graph_State) -> bool:
    original_query = (
        state.input_request.user_query if state.input_request is not None else ""
    )
    classified_query = " ".join(state.banksql_queries or [])
    return is_explicit_pie_request(original_query) or is_explicit_pie_request(
        classified_query
    )


def _infer_explicit_chart(
    rows: list[dict[str, Any]],
    user_query: str = "",
    requested_chart_type: str | None = None,
) -> tuple[VisualizationDecision, list[dict[str, Any]]] | None:
    if not rows or any(set(row) != set(rows[0]) for row in rows):
        return None
    rows = [dict(row) for row in rows]

    fields = list(rows[0])
    numeric_fields = [
        field
        for field in fields
        if all(_numeric_value(row[field]) is not None for row in rows)
        and field.lower() not in {"year", "month", "day"}
        and "id" not in field.lower()
        and "card" not in field.lower()
    ]
    if not numeric_fields:
        return None

    query = user_query.casefold()
    preferred_measure = None
    if re.search(r"\b(?:balance|balances)\b", query):
        preferred_measure = "balance"
    elif re.search(r"\b(?:rate|ratio|percent|percentage|apr)\b", query):
        preferred_measure = "rate"
    elif re.search(r"\b(?:count|how many|number of)\b", query):
        preferred_measure = "count"
    elif re.search(r"\b(?:spend|spending|spent|amount|cost|purchase)\b", query):
        preferred_measure = "amount"

    def y_score(field: str) -> tuple[int, str]:
        name = field.lower()
        if preferred_measure == "balance" and "balance" in name:
            return (0, name)
        if preferred_measure == "rate" and any(
            term in name for term in ("rate", "ratio", "percent", "apr")
        ):
            return (0, name)
        if preferred_measure == "count" and "count" in name:
            return (0, name)
        if preferred_measure == "amount" and any(
            term in name for term in ("amount", "spent", "spend", "cost")
        ):
            return (0, name)
        if any(term in name for term in ("amount", "spent", "spend")):
            return (1, name)
        if "balance" in name:
            return (2, name)
        if "total" in name:
            return (3, name)
        if "count" in name:
            return (4, name)
        return (5, name)

    y_field = min(numeric_fields, key=y_score)
    currency, mixed_currency = _currency_info(rows, y_field)
    if mixed_currency:
        return None

    mode = _aggregation_mode(y_field)
    time_field = next(
        (field for field in ("transaction_date", "date", "year_month") if field in fields),
        None,
    )
    if time_field is None and {"year", "month", "day"}.issubset(fields):
        time_field = "_chart_period"
        for row in rows:
            row[time_field] = (
                f"{int(row['year']):04}-{int(row['month']):02}-"
                f"{int(row['day']):02}"
            )
    elif time_field is None and {"year", "month"}.issubset(fields):
        time_field = "_chart_period"
        for row in rows:
            row[time_field] = f"{int(row['year']):04}-{int(row['month']):02}"
    elif time_field is None and "month" in fields and "year" not in fields:
        time_field = "month"

    category_field = next(
        (
            field
            for field in (
                "merchant_brand_name",
                "merchant_system_category1_name",
                "merchant_system_category2_name",
                "country",
                "channel",
                "card_type",
                "category",
                "merchant",
            )
            if field in fields
        ),
        None,
    )
    if category_field == y_field:
        category_field = None

    x_field = (
        category_field
        if requested_chart_type == "pie" or mode == "latest" and category_field
        else time_field or category_field
    )
    if requested_chart_type == "pie" and category_field is None:
        return None
    if x_field is None:
        x_field = "_chart_category"
        for index, row in enumerate(rows, start=1):
            row[x_field] = "Result" if len(rows) == 1 else f"Result {index}"

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        category = str(row[x_field]).strip()
        if not category:
            continue
        grouped.setdefault(category, []).append(row)

    chart_rows = []
    for category, category_rows in grouped.items():
        if mode == "latest" and len(category_rows) > 1:
            date_field = next(
                (
                    field
                    for field in (
                        "transaction_date",
                        "date",
                        "year_month",
                        "_chart_period",
                    )
                    if field in category_rows[0]
                ),
                None,
            )
            if date_field is None:
                return None
            latest = max(category_rows, key=lambda row: _time_sort_key(row[date_field]))
            value = float(latest[y_field])
            source_row = latest
        elif mode == "sum":
            value = sum(float(row[y_field]) for row in category_rows)
            source_row = category_rows[0]
        else:
            value = sum(float(row[y_field]) for row in category_rows) / len(category_rows)
            source_row = category_rows[0]
        aggregate = {x_field: category, y_field: value}
        for currency_field in CURRENCY_FIELDS:
            if currency_field in source_row:
                aggregate[currency_field] = source_row[currency_field]
        chart_rows.append(aggregate)
    if not chart_rows:
        return None

    chart_type = requested_chart_type or (
        "line" if x_field.lower() in TIME_FIELDS and len(chart_rows) >= 2 else "bar"
    )
    if chart_type == "line":
        chart_rows = _downsample_line_rows(
            chart_rows, x_field, y_field, MAX_LINE_POINTS
        )
    elif chart_type == "pie":
        omitted_count = 0
    elif chart_type == "bar" and len(chart_rows) > MAX_BAR_CATEGORIES:
        chart_rows, omitted_count = _collapse_bar_categories(chart_rows, x_field, y_field)
    else:
        omitted_count = 0

    title = f"{y_field.replace('_', ' ').title()} by {x_field.replace('_', ' ').title()}"
    if currency:
        title += f" ({currency})"
    if chart_type == "bar" and omitted_count:
        title += f" ({omitted_count} categories grouped as Other)"
    decision = VisualizationDecision(
        should_visualize=True,
        chart_type=chart_type,
        chart_title=title,
        x_field=x_field,
        y_field=y_field,
        reasoning="Chart created because the user explicitly requested a visualization.",
    )
    return decision, chart_rows


async def visualization_agent(
    state: Graph_State,
    llm: Any,
    prompt: Any,
    logger: Logger,
) -> Graph_State:
    logger.info("Executing visualization agent.")
    try:
        explicit_request = _explicit_chart_requested(state)
        rows = state.database_query_results or []
        fallback_active = state.fallback_response and state.fallback_response.fallback_flag

        if not rows or fallback_active:
            state.visualization_response = VisualizationResponse(
                decision=VisualizationDecision(
                    should_visualize=False,
                    chart_type="none",
                    chart_title="",
                    x_field=None,
                    y_field=None,
                    reasoning="No data available or fallback active.",
                ),
                plotly_json=None,
            )
            return state

        if any(not isinstance(row, dict) for row in rows):
            logger.warning("Visualization skipped: database results are not structured rows.")
            state.visualization_response = VisualizationResponse(
                decision=VisualizationDecision(
                    should_visualize=False,
                    chart_type="none",
                    chart_title="",
                    x_field=None,
                    y_field=None,
                    reasoning="Database results are not structured rows.",
                ),
                plotly_json=None,
            )
            return state

        if state.database_results_truncated:
            logger.warning(
                "Visualization skipped because the SQL result set was truncated."
            )
            state.visualization_response = _skip_viz(
                "The query results were truncated, so a chart could misrepresent "
                "the complete data. Narrow the date range or request fewer categories."
            )
            return state

        if explicit_request:
            request_text = " ".join(
                [
                    state.input_request.user_query if state.input_request else "",
                    *(state.banksql_queries or []),
                ]
            )
            explicit_pie = _explicit_pie_requested(state)
            inferred_chart = _infer_explicit_chart(
                rows,
                request_text,
                requested_chart_type="pie" if explicit_pie else None,
            )
            if inferred_chart is None:
                reason = (
                    _pie_alternative_message(
                        "the results do not contain a usable categorical measure."
                    )
                    if explicit_pie
                    else (
                        "A chart was requested, but the query results do not contain "
                        "a single-currency numeric measure that can be charted."
                    )
                )
                state.visualization_response = _skip_viz(reason)
                return state
            decision, chart_rows = inferred_chart
            validation_error = _validate_chart_data(
                decision, chart_rows, explicit_request=True
            )
            if validation_error:
                reason = (
                    _pie_alternative_message(validation_error)
                    if decision.chart_type == "pie"
                    else validation_error
                )
                state.visualization_response = _skip_viz(reason, decision)
                return state
            if decision.chart_type == "pie":
                if decision.x_field is None or decision.y_field is None:
                    state.visualization_response = _skip_viz(
                        _pie_alternative_message(
                            "the results do not contain both a category and a numeric measure."
                        ),
                        decision,
                    )
                    return state
                chart_rows, omitted_count = _collapse_pie_categories(
                    chart_rows, decision.x_field, decision.y_field
                )
                if omitted_count:
                    decision = decision.model_copy(
                        update={
                            "chart_title": (
                                f"{decision.chart_title} "
                                f"(top {MAX_PIE_CATEGORIES} + Other)"
                            )
                        }
                    )
            plotly_json = _build_plotly_figure(
                decision, chart_rows, logger, explicit_request=True
            )
            if plotly_json is None:
                state.visualization_response = _skip_viz(
                    "A chart was requested, but its results could not be rendered.",
                    decision,
                )
            else:
                state.visualization_response = VisualizationResponse(
                    decision=decision, plotly_json=plotly_json
                )
                state.visualization_only = True
            return state

        # Avoid a model call for result sets too small to make a useful chart.
        if len(rows) < 2:
            logger.info(
                "Skipping visualization: only %s row(s); at least two are required.",
                len(rows),
            )
            state.visualization_response = _skip_viz(
                f"Only {len(rows)} value(s) — too few for a meaningful chart."
            )
            return state

        if any(set(row) != set(rows[0]) for row in rows):
            state.visualization_response = _skip_viz(
                "Result rows do not share a consistent set of fields."
            )
            return state

        columns = sorted(set.intersection(*(set(row) for row in rows)))
        column_types = {
            column: (
                "numeric"
                if all(_numeric_value(row[column]) is not None for row in rows)
                else "text or mixed"
            )
            for column in columns
        }
        # Use the classified query/queries being answered on the SQL path.
        if state.banksql_queries:
            user_query = " ".join(state.banksql_queries)
        elif state.context_builder_response:
            user_query = " ".join(state.context_builder_response.contextual_queries)
        else:
            user_query = ""

        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": prompt.template,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
            },
            {
                "role": "user",
                "content": (
                    f"User question: {user_query}\n"
                    f"Available columns and types: {column_types}\n"
                    f"Number of rows: {len(rows)}\n"
                ),
            },
        ]

        structured_llm = llm.with_structured_output(VisualizationDecision, include_raw=True)

        start_time = time.time()
        response_raw = await structured_llm.ainvoke(messages)
        latency = time.time() - start_time

        decision: VisualizationDecision = response_raw["parsed"]
        response_message = response_raw["raw"]

        metric_metadata = agent_utils.agent_metrics(response_message, latency)
        if state.agent_metrics is not None:
            state.agent_metrics["visualization_agent"] = metric_metadata

        logger.info(f"Visualization agent full response: {decision.model_dump()}")

        if not decision.should_visualize or decision.chart_type == "none":
            state.visualization_response = VisualizationResponse(
                decision=decision, plotly_json=None
            )
            return state

        validation_error = _validate_chart_data(decision, rows)
        if validation_error:
            logger.info(
                "Skipping invalid visualization decision (%s): %s",
                decision.chart_type,
                validation_error,
            )
            state.visualization_response = _skip_viz(validation_error, decision)
            return state

        if decision.chart_type == "pie":
            if decision.x_field is None or decision.y_field is None:
                state.visualization_response = _skip_viz(
                    "Pie charts require a category and a numeric measure.", decision
                )
                return state
            original_category_count = len(rows)
            rows, omitted_count = _collapse_pie_categories(
                rows, decision.x_field, decision.y_field
            )
            if omitted_count:
                decision = decision.model_copy(
                    update={
                        "chart_title": (
                            f"{decision.chart_title} "
                            f"(top {MAX_PIE_CATEGORIES} + Other)"
                        ),
                        "reasoning": (
                            f"{decision.reasoning} The pie shows the top "
                            f"{MAX_PIE_CATEGORIES} of {original_category_count} "
                            f"categories and combines the remaining {omitted_count} "
                            "into Other."
                        ),
                    }
                )

        if decision.chart_type == "line" and len(rows) > MAX_LINE_POINTS:
            if decision.x_field is None:
                state.visualization_response = _skip_viz(
                    "The chart decision must specify an x field.", decision
                )
                return state
            original_row_count = len(rows)
            rows = _downsample_line_rows(
                rows, decision.x_field, decision.y_field, MAX_LINE_POINTS
            )
            decision = decision.model_copy(
                update={
                    "chart_title": f"{decision.chart_title} (sampled)",
                    "reasoning": (
                        f"{decision.reasoning} The chart shows "
                        f"{MAX_LINE_POINTS} points selected to preserve series peaks and dips, "
                        "including the endpoints."
                    ),
                }
            )
            logger.info(
                "Downsampled line chart from %s to %s points.",
                original_row_count,
                len(rows),
            )
        elif decision.chart_type == "bar" and len(rows) > MAX_BAR_CATEGORIES:
            if decision.x_field is None or decision.y_field is None:
                state.visualization_response = _skip_viz(
                    "The chart decision must specify both x and y fields.", decision
                )
                return state
            original_category_count = len(rows)
            rows, omitted_count = _collapse_bar_categories(
                rows, decision.x_field, decision.y_field
            )
            decision = decision.model_copy(
                update={
                    "chart_title": f"{decision.chart_title} (Other groups {omitted_count} categories)",
                    "reasoning": (
                        f"{decision.reasoning} The chart combines the {omitted_count} "
                        f"smallest of {original_category_count} categories into Other."
                    ),
                }
            )

        plotly_json = _build_plotly_figure(decision, rows, logger)
        if plotly_json is None:
            state.visualization_response = _skip_viz(
                "The chart could not be rendered from the selected fields.", decision
            )
            return state
        state.visualization_response = VisualizationResponse(
            decision=decision, plotly_json=plotly_json
        )

    except Exception as e:
        logger.exception(f"Error in visualization agent: {e}")
        state.visualization_response = VisualizationResponse(
            decision=VisualizationDecision(
                should_visualize=False,
                chart_type="none",
                chart_title="",
                x_field=None,
                y_field=None,
                reasoning=f"Agent error: {e}",
            ),
            plotly_json=None,
        )

    return state


def _downsample_line_rows(
    rows: list[dict[str, Any]], x_field: str, y_field: str, max_points: int
) -> list[dict[str, Any]]:
    if max_points < 2:
        raise ValueError("Line chart downsampling requires at least two points.")

    ordered_rows = sorted(rows, key=lambda row: _time_sort_key(row[x_field]))
    if len(ordered_rows) <= max_points:
        return ordered_rows
    if max_points == 2:
        return [ordered_rows[0], ordered_rows[-1]]

    bucket_width = (len(ordered_rows) - 2) / (max_points - 2)
    selected_indices = [0]
    previous_index = 0

    for bucket_index in range(max_points - 2):
        average_start = int(math.floor((bucket_index + 1) * bucket_width)) + 1
        average_end = int(math.floor((bucket_index + 2) * bucket_width)) + 1
        average_end = min(average_end, len(ordered_rows))
        average_rows = ordered_rows[average_start:average_end]
        if not average_rows:
            average_rows = [ordered_rows[-1]]
        average_x = sum(
            average_start + index for index in range(len(average_rows))
        ) / len(average_rows)
        average_y = sum(float(row[y_field]) for row in average_rows) / len(average_rows)

        range_start = int(math.floor(bucket_index * bucket_width)) + 1
        range_end = int(math.floor((bucket_index + 1) * bucket_width)) + 1
        range_end = min(max(range_end, range_start + 1), len(ordered_rows) - 1)
        previous = ordered_rows[previous_index]
        ax = float(previous_index)
        ay = float(previous[y_field])
        best_area = -1.0
        best_index = range_start
        for candidate_index in range(range_start, range_end):
            candidate = ordered_rows[candidate_index]
            candidate_y = float(candidate[y_field])
            area = abs(
                (ax - average_x) * (candidate_y - ay)
                - (ax - candidate_index) * (average_y - ay)
            )
            if area > best_area:
                best_area = area
                best_index = candidate_index
        selected_indices.append(best_index)
        previous_index = best_index

    selected_indices.append(len(ordered_rows) - 1)
    return [ordered_rows[index] for index in selected_indices]


def _build_plotly_figure(
    decision: VisualizationDecision,
    rows: list[dict[str, Any]],
    logger: Logger,
    *,
    explicit_request: bool = False,
) -> str | None:
    try:
        validation_error = _validate_chart_data(
            decision, rows, explicit_request=explicit_request
        )
        if validation_error:
            logger.info("Cannot build chart: %s", validation_error)
            return None

        x_field = decision.x_field
        y_field = decision.y_field
        if x_field is None or y_field is None:
            return None

        ui_colors = ["#7c3aed", "#a78bfa", "#6d28d9", "#ec4899", "#10b981", "#4f46e5", "#64748b"]

        label_map = {
            "merchant_system_category1_name": "Category",
            "merchant_system_category2_name": "Sub-Category",
            "merchant_brand_name": "Merchant",
            "amount": "Amount",
            "total_spent": "Total Spent",
            "total": "Total",
            "transaction_date": "Date",
            "month": "Month",
            "year": "Year",
            "day": "Day",
            "transaction_type": "Transaction Type",
            "balance": "Balance",
            "current_balance": "Current Balance",
            "total_income": "Total Income",
            "count": "Count",
            "transaction_id": "Transaction ID",
            "customer_id": "Customer",
        }

        chart_type = decision.chart_type
        currency, _ = _currency_info(rows, y_field)
        normalized_y_field = re.sub(
            r"_(aud|cad|chf|cny|eur|gbp|inr|jpy|nzd|usd)$",
            "",
            y_field.lower(),
        )
        y_label = label_map.get(
            normalized_y_field, normalized_y_field.replace("_", " ").title()
        )
        if currency:
            y_label = f"{y_label} ({currency})"

        chart_rows = rows
        if chart_type == "line":
            def sort_key(row: dict[str, Any]) -> tuple[int, Any]:
                value = row[x_field]
                numeric = _numeric_value(value)
                return (0, numeric) if numeric is not None else (1, str(value))

            chart_rows = sorted(rows, key=sort_key)

        x_vals = [str(row[x_field]) for row in chart_rows]
        y_vals = [_numeric_value(row[y_field]) for row in chart_rows]
        if any(value is None for value in y_vals):
            return None
        numeric_y_vals = [value for value in y_vals if value is not None]
        value_label = f"{currency} " if currency else ""
        y_format = (
            ",.2f"
            if currency or any(not value.is_integer() for value in numeric_y_vals)
            else ",.0f"
        )
        hover_template = (
            f"%{{x}}<br><b>{value_label}%{{y:{y_format}}}</b><extra></extra>"
        )

        common_layout = go.Layout(
            title=dict(text=decision.chart_title, font=dict(size=15, color="#1f2937", family="Hanken Grotesk"), x=0.02, xanchor="left"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Hanken Grotesk", color="#374151", size=12),
            margin=dict(l=65, r=30, t=55, b=75),
            colorway=ui_colors,
        )

        if chart_type == "pie":
            fig = go.Figure(
                data=[
                    go.Pie(
                        labels=x_vals,
                        values=numeric_y_vals,
                        hole=0.28,
                        sort=False,
                        textinfo="label+percent",
                        textposition="inside",
                        marker=dict(
                            colors=ui_colors,
                            line=dict(color="#ffffff", width=2),
                        ),
                        hovertemplate=(
                            f"%{{label}}<br><b>{value_label}%{{value:{y_format}}}</b>"
                            "<br>%{percent}<extra></extra>"
                        ),
                    )
                ],
                layout=common_layout,
            )
            fig.update_layout(
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.08,
                    xanchor="center",
                    x=0.5,
                ),
                margin=dict(l=20, r=20, t=55, b=70),
            )

        elif chart_type == "bar":
            fig = go.Figure(
                data=[
                    go.Bar(
                        x=x_vals,
                        y=numeric_y_vals,
                        marker_color="#7c3aed",
                        marker_line_color="#6d28d9",
                        marker_line_width=1,
                        opacity=0.88,
                        text=[
                            f"{v:,.2f}"
                            if currency or not v.is_integer()
                            else f"{v:,.0f}"
                            for v in numeric_y_vals
                        ],
                        textposition="outside",
                        textfont=dict(size=11, color="#6d28d9", family="Hanken Grotesk"),
                        cliponaxis=False,
                        hovertemplate=hover_template,
                    )
                ],
                layout=common_layout,
            )
            fig.update_layout(
                margin=dict(l=65, r=30, t=55, b=90),
                xaxis=dict(
                    showgrid=False,
                    tickangle=-30,
                ),
                yaxis=dict(
                    title=dict(text=y_label, font=dict(size=12, color="#6b7280")),
                    showgrid=True,
                    gridcolor="rgba(124,58,237,0.06)",
                ),
                uniformtext=dict(minsize=9, mode="hide"),
            )

        elif chart_type == "line":
            fig = go.Figure(
                data=[
                    go.Scatter(
                        x=x_vals,
                        y=numeric_y_vals,
                        mode="lines+markers",
                        line=dict(color="#7c3aed", width=2.5, shape="spline", smoothing=0.8),
                        marker=dict(
                            color="#ffffff",
                            size=9,
                            line=dict(color="#7c3aed", width=2.5),
                            symbol="circle",
                        ),
                        fill="tozeroy",
                        fillcolor="rgba(124,58,237,0.10)",
                        hovertemplate=hover_template,
                    )
                ],
                layout=common_layout,
            )
            fig.update_layout(
                xaxis=dict(
                    showgrid=False,
                    tickangle=-30,
                    showline=True,
                    linecolor="rgba(0,0,0,0.08)",
                ),
                yaxis=dict(
                    title=dict(text=y_label, font=dict(size=12, color="#6b7280")),
                    showgrid=True,
                    gridcolor="rgba(124,58,237,0.06)",
                    zeroline=False,
                ),
            )

        else:
            return None

        return pio.to_json(fig)

    except Exception as e:
        logger.exception(f"Error building Plotly figure: {e}")
        return None
