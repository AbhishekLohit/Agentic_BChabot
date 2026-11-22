"""Chart Eligibility Rules — the single owner of the Transaction Charts rules.

Spine AD-17 / AD-19 / AD-20. Pure functions and constants only: stdlib
(``ast``, ``re``, ``datetime``) plus the chart models from ``app_models``.
No LLM, SQL or network call. The same inputs always give the same
``ChartResponse``.

Entry point: :func:`decide_chart`. It reads only the Result
(``database_query_results``), ``user_query``, ``banksql_queries`` and
``fallback_response`` — never the SQL text — and applies the AD-20 decision
table, first matching row wins.

Not here yet: ``EXPLICIT_CHART_PATTERN`` (Story 2.1) and ``CHART_NOTES``
(Story 2.2). Until then ``explicit_request`` is always ``False``.
"""

import ast
import re
from datetime import date

from app_models import ChartPoint, ChartResponse, ChartSpec


# ---------------------------------------------------------------------------
# AD-20 column convention (changes together with the text2sql and
# sql_correction prompts; clear query_cache and Redis qcache:* afterwards).
# ---------------------------------------------------------------------------
LABEL_COLUMNS = ("merchant", "category", "subcategory", "period")
VALUE_COLUMNS = ("spending", "income", "net", "balance")
PERIOD_RANGE_COLUMNS = ("period_start", "period_end")
PERIOD_LABEL_COLUMN = "period"

PER_TRANSACTION_COLUMNS = (
    "transaction_id",
    "transaction_date",
    "card_number",
    "transaction_type",
    "amount_usd",
)

# The four `period` grains, as the strftime format the SQL must use and the
# regex the rules match against. A Time Point is whatever the SQL grouped by.
PERIOD_GRAIN_FORMATS = {
    "year": "%Y",
    "month": "%Y-%m",
    "week": "%G-W%V",
    "day": "%Y-%m-%d",
}
PERIOD_GRAIN_PATTERNS = {
    "year": re.compile(r"^(\d{4})$"),
    "month": re.compile(r"^(\d{4})-(\d{2})$"),
    "week": re.compile(r"^(\d{4})-W(\d{2})$"),
    "day": re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),
}

# Limits (CAP-1, CAP-2). Above the max nothing is truncated, bucketed or regrouped.
MIN_CATEGORIES = 3
MAX_CATEGORIES = 8
MIN_TIME_POINTS = 3
MAX_TIME_POINTS = 12

# AD-10 notice appended by sql_execution when it cuts a Result to 20 rows.
TRUNCATION_NOTICE_PREFIX = "[System Notification:"

# ---------------------------------------------------------------------------
# Title / axis / label tables (AD-19), keyed by AD-20 column names.
# ---------------------------------------------------------------------------
MEASURE_TITLES = {
    "spending": "Spending",
    "income": "Income",
    "net": "Net",
    "balance": "Balance",
}
GROUPING_TITLES = {
    "merchant": "Merchant",
    "category": "Category",
    "subcategory": "Subcategory",
}
PERIOD_GRAIN_TITLES = {
    "year": "Year",
    "month": "Month",
    "week": "Week",
    "day": "Day",
}
VALUE_AXIS_SUFFIX = "(USD)"
TITLE_SEPARATOR = " — "
PERIOD_RANGE_JOINER = " to "

_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_FULL = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")

_INF = float("inf")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def decide_chart(database_query_results, user_query, banksql_queries, fallback_response) -> ChartResponse:
    """Apply the AD-20 decision table and return the Chart Decision.

    ``user_query`` is part of the contract for the explicit-request pattern
    (AD-21, Story 2.1) and is not read yet.
    """
    try:
        return _decide(database_query_results, banksql_queries, fallback_response)
    except Exception:
        # Row 1: any exception on the chart path. Built from literals only (AD-18).
        return chart_error_response()


def chart_error_response() -> ChartResponse:
    """The fixed outcome for a chart-path failure (AD-18)."""
    return ChartResponse(decision="none", skip_reason="chart_error", explicit_request=False, spec=None)


def note_due(chart_response: ChartResponse | None) -> bool:
    """The one predicate for the FR-5 note (AD-21)."""
    if chart_response is None:
        return False
    return (
        chart_response.explicit_request
        and chart_response.decision == "none"
        and chart_response.skip_reason != "chart_error"
    )


# ---------------------------------------------------------------------------
# Decision table
# ---------------------------------------------------------------------------
def _skip(reason: str) -> ChartResponse:
    return ChartResponse(decision="none", skip_reason=reason, explicit_request=False)


def _decide(database_query_results, banksql_queries, fallback_response) -> ChartResponse:
    # Row 2
    if fallback_response is not None:
        return _skip("fallback")
    # Row 3
    if not banksql_queries:
        return _skip("no_transaction_data")

    rows, truncated = _parse_results(database_query_results)

    # Row 4
    if not rows or all(v is None for row in rows for v in row.values()):
        return _skip("empty_result")

    columns = set()
    for row in rows:
        columns.update(row.keys())

    # Row 5
    if columns & set(PER_TRANSACTION_COLUMNS):
        return _skip("raw_list")
    uses_convention = bool(columns & set(LABEL_COLUMNS)) and bool(columns & set(VALUE_COLUMNS))
    if truncated and not uses_convention:
        return _skip("raw_list")

    # Row 6
    if not truncated and len(rows) < min(MIN_CATEGORIES, MIN_TIME_POINTS):
        return _skip("too_few_points")

    # Row 7
    shape = _check_shape(rows, columns)
    if shape is None:
        return _skip("unsuitable_shape")
    label_col, value_col, grain = shape
    is_period = label_col == PERIOD_LABEL_COLUMN

    # Rows 8 and 9
    if not is_period and (truncated or len(rows) > MAX_CATEGORIES):
        return _skip("too_many_categories")
    if is_period and (truncated or len(rows) > MAX_TIME_POINTS):
        return _skip("too_many_time_points")

    # Row 10
    spec = _build_spec(rows, label_col, value_col, grain)
    return ChartResponse(decision=spec.chart_type, skip_reason=None, explicit_request=False, spec=spec)


def _parse_results(database_query_results):
    """Set aside the AD-10 notice, then literal_eval each row into a dict."""
    if not database_query_results:
        return [], False
    truncated = False
    rows = []
    for item in database_query_results:
        if isinstance(item, str) and item.lstrip().startswith(TRUNCATION_NOTICE_PREFIX):
            truncated = True
            continue
        row = ast.literal_eval(item)
        if not isinstance(row, dict):
            raise ValueError("result row is not a dict")
        rows.append(row)
    return rows, truncated


def _is_finite_number(v) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return v == v and v not in (_INF, -_INF)


def _check_shape(rows, columns):
    """Return (label_col, value_col, grain) for a convention-shaped Result, else None."""
    label_cols = [c for c in columns if c in LABEL_COLUMNS]
    value_cols = [c for c in columns if c in VALUE_COLUMNS]
    other_cols = columns - set(LABEL_COLUMNS) - set(VALUE_COLUMNS) - set(PERIOD_RANGE_COLUMNS)
    if len(label_cols) != 1 or len(value_cols) != 1 or other_cols:
        return None
    label_col, value_col = label_cols[0], value_cols[0]

    labels = []
    for row in rows:
        if label_col not in row or value_col not in row:
            return None
        label = row[label_col]
        if not isinstance(label, str) or not label.strip():
            return None
        if not _is_finite_number(row[value_col]):
            return None
        labels.append(label)
    if len(set(labels)) != len(labels):
        return None

    grain = None
    if label_col == PERIOD_LABEL_COLUMN:
        grains = {_period_grain(p) for p in labels}
        if None in grains or len(grains) != 1:
            return None
        grain = grains.pop()
    return label_col, value_col, grain


def _period_grain(period: str):
    """Return the grain a `period` string matches (and is a real date at), else None."""
    for grain, pattern in PERIOD_GRAIN_PATTERNS.items():
        m = pattern.match(period)
        if not m:
            continue
        try:
            parts = [int(g) for g in m.groups()]
            if grain == "year":
                date(parts[0], 1, 1)
            elif grain == "month":
                date(parts[0], parts[1], 1)
            elif grain == "week":
                date.fromisocalendar(parts[0], parts[1], 1)
            else:
                date(parts[0], parts[1], parts[2])
        except ValueError:
            return None
        return grain
    return None


# ---------------------------------------------------------------------------
# ChartSpec building
# ---------------------------------------------------------------------------
def _build_spec(rows, label_col, value_col, grain) -> ChartSpec:
    measure = MEASURE_TITLES[value_col]
    y_label = f"{measure} {VALUE_AXIS_SUFFIX}"

    if label_col == PERIOD_LABEL_COLUMN:
        ordered = sorted(rows, key=lambda r: r[label_col])  # raw ISO period sorts chronologically
        points = [
            ChartPoint(label=format_period_label(r[label_col], grain), value=round(float(r[value_col]), 2))
            for r in ordered
        ]
        grouping = PERIOD_GRAIN_TITLES[grain]
        first, last = points[0].label, points[-1].label
        phrase = first if first == last else f"{first}{PERIOD_RANGE_JOINER}{last}"
        return ChartSpec(
            chart_type="line",
            title=f"{measure} by {grouping.lower()}{TITLE_SEPARATOR}{phrase}",
            x_label=grouping,
            y_label=y_label,
            points=points,
        )

    points = [
        ChartPoint(label=r[label_col].strip(), value=round(float(r[value_col]), 2))
        for r in rows
    ]
    grouping = GROUPING_TITLES[label_col]
    title = f"{measure} by {grouping.lower()}"
    phrase = _bar_period_phrase(rows)
    if phrase:
        title = f"{title}{TITLE_SEPARATOR}{phrase}"
    return ChartSpec(chart_type="bar", title=title, x_label=grouping, y_label=y_label, points=points)


def format_period_label(period: str, grain: str) -> str:
    """Display label for a raw ISO period, e.g. '2026-03' -> 'Mar 2026'."""
    m = PERIOD_GRAIN_PATTERNS[grain].match(period)
    parts = [int(g) for g in m.groups()]
    if grain == "year":
        return str(parts[0])
    if grain == "month":
        return f"{_MONTH_ABBR[parts[1] - 1]} {parts[0]}"
    if grain == "week":
        return f"Wk {parts[1]} {parts[0]}"
    return f"{parts[2]} {_MONTH_ABBR[parts[1] - 1]} {parts[0]}"


def _parse_iso_date(value):
    """Leading YYYY-MM-DD of a date/datetime string, else None."""
    if not isinstance(value, str):
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _bar_period_phrase(rows):
    """Period phrase from period_start/period_end, or None when absent/unusable."""
    starts = [_parse_iso_date(r.get("period_start")) for r in rows if "period_start" in r]
    ends = [_parse_iso_date(r.get("period_end")) for r in rows if "period_end" in r]
    starts = [d for d in starts if d is not None]
    ends = [d for d in ends if d is not None]
    if not starts or not ends:
        return None
    start, end = min(starts), max(ends)
    if end < start:
        return None
    if start == end:
        return _day_label(start)
    if start.year == end.year and start.month == end.month and start.day == 1 and _is_month_end(end):
        return f"{_MONTH_FULL[start.month - 1]} {start.year}"
    if (start.month, start.day) == (1, 1) and (end.month, end.day) == (12, 31) and start.year == end.year:
        return str(start.year)
    return f"{_day_label(start)}{PERIOD_RANGE_JOINER}{_day_label(end)}"


def _day_label(d: date) -> str:
    return f"{d.day} {_MONTH_ABBR[d.month - 1]} {d.year}"


def _is_month_end(d: date) -> bool:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return (nxt - d).days == 1
