
"""Deterministic scoring of LangGraph states (no LLM judge or credentials needed)."""

from __future__ import annotations

import ast
import math
import re
import sqlite3
from collections import Counter
from typing import Any


VALID_ROUTES = {
    "BankSQL",
    "HelpCenter",
    "Out_of_scope",
    "guardrail",
}


def field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def classify_routes(state: dict) -> set[str]:
    routes = set()

    if state.get("banksql_queries"):
        routes.add("BankSQL")

    if state.get("help_center_queries"):
        routes.add("HelpCenter")

    if state.get("out_of_scope_queries"):
        routes.add("Out_of_scope")

    fallback = state.get("fallback_response")

    if field(fallback, "fallback_node") == "guardrail":
        routes.add("guardrail")

    return routes


def parse_result_values(
    items: list[str] | None
) -> list[Any]:
    """Parse SQL executor results without evaluating code."""

    rows = []

    for item in items or []:
        try:
            row = ast.literal_eval(item)
        except (
            ValueError,
            SyntaxError,
            TypeError,
            MemoryError,
        ):
            return []

        if not isinstance(row, dict):
            return []

        rows.append(list(row.values()))

    return rows


def _canonical(value: Any) -> Any:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return (
            round(float(value), 5)
            if math.isfinite(float(value))
            else str(value)
        )

    return (
        str(value).strip().casefold()
        if value is not None
        else None
    )


def compare_results(
    actual: list[list[Any]],
    expected: list[list[Any]],
    *,
    unordered: bool = False,
) -> bool:
    """Compare SQL values independently of column aliases."""

    def convert(rows):
        return [
            tuple(_canonical(v) for v in row)
            for row in rows
        ]

    lhs = convert(actual)
    rhs = convert(expected)

    return (
        Counter(lhs) == Counter(rhs)
        if unordered
        else lhs == rhs
    )


def expected_sql_values(
    conn: sqlite3.Connection,
    query: str,
) -> list[list[Any]]:
    """Execute a trusted reference SELECT query."""

    cleaned = query.strip().rstrip(";").strip()

    if (
        not re.match(
            r"^(SELECT|WITH)\b",
            cleaned,
            re.IGNORECASE,
        )
        or ";" in cleaned
    ):
        raise ValueError(
            "Reference SQL must be a single SELECT or WITH query"
        )

    cursor = conn.execute(cleaned)

    if cursor.description is None:
        raise ValueError(
            "Reference SQL returned no columns"
        )

    return [list(row) for row in cursor.fetchall()]


def score_case(
    case: dict,
    state: dict,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Return criterion-level evaluation results."""

    checks: dict[str, bool] = {}

    observed_routes = classify_routes(state)
    expected_routes = set(
        case.get("expected_routes", [])
    )

    # Routing evaluation
    if expected_routes:
        checks["routing"] = (
            observed_routes == expected_routes
        )

    # SQL evaluation
    sql_resp = state.get(
        "text2sql_generator_response"
    )

    sql = field(
        sql_resp,
        "sql_query",
        "",
    ) or ""

    if case.get("expected_sql"):
        if conn is None:
            raise ValueError(
                "DB connection required to score expected_sql"
            )

        expected = expected_sql_values(
            conn,
            case["expected_sql"],
        )

        actual = parse_result_values(
            state.get("database_query_results")
        )

        checks["sql_result"] = compare_results(
            actual,
            expected,
            unordered=case.get("unordered", False),
        )

        checks["sql_read_only"] = bool(
            re.match(
                r"^\s*(SELECT|WITH)\b",
                sql,
                re.IGNORECASE,
            )
        )

    # Fallback information
    fallback = state.get("fallback_response")

    fallback_flag = bool(
        field(
            fallback,
            "fallback_flag",
            False,
        )
    )

    raw_reason = field(
        fallback,
        "fallback_reason",
        None,
    )

    # Only record recognized categories.
    # Avoid exposing arbitrary exception details.
    known_reasons = {
        "SYSTEM_VIOLATION",
        "SQL_VIOLATION",
        "PII_VIOLATION",
        "OUT_OF_SCOPE",
        "greeting",
        "exception",
    }

    fallback_reason = (
        raw_reason
        if raw_reason in known_reasons
        else "other" if raw_reason else None
    )

    response = field(
        state.get("response_generation_response"),
        "generated_response",
        "",
    ) or ""

    fallback_message = field(
        fallback,
        "fallback_message",
        "",
    ) or ""

    # For positive keyword checks, use the visible answer.
    answer = response or fallback_message

    # For exclusion checks, inspect both possible outputs.
    all_output = f"{response}\n{fallback_message}"

    # Response quality
    if case.get("answer_contains"):
        checks["answer_keywords"] = all(
            term.casefold() in answer.casefold()
            for term in case["answer_contains"]
        )

    if case.get("answer_excludes"):
        checks["answer_excludes"] = all(
            term.casefold() not in all_output.casefold()
            for term in case["answer_excludes"]
        )

    # Fallback evaluation
    if "expected_fallback" in case:
        checks["fallback"] = (
            fallback_flag == case["expected_fallback"]
        )

        if case["expected_fallback"]:
            checks["fallback_no_exception"] = (
                fallback_reason is not None
                and fallback_reason != "exception"
            )

    # Agent metrics
    metrics = state.get("agent_metrics") or {}

    total_tokens = sum(
        int(
            field(m, "total_tokens", 0) or 0
        )
        for m in metrics.values()
    )

    cost = sum(
        float(
            field(m, "model_cost_with_cache", 0) or 0
        )
        for m in metrics.values()
    )

    return {
        "id": case["id"],
        "category": case["category"],
        "checks": checks,
        "passed": bool(checks) and all(
            checks.values()
        ),
        "total_tokens": total_tokens,
        "estimated_model_cost": round(
            cost,
            6,
        ),
        "routes_observed": sorted(
            observed_routes
        ),
        "fallback_reason": fallback_reason,
        "has_sql": bool(sql),
    }


def summarize(results: list[dict]) -> dict:
    total = len(results)

    counts: dict[str, list[bool]] = {}

    for result in results:
        for name, passed in result.get(
            "checks",
            {},
        ).items():
            counts.setdefault(
                name,
                [],
            ).append(passed)

    passed_count = sum(
        r["passed"] for r in results
    )

    return {
        "total": total,
        "passed": passed_count,
        "pass_rate": (
            round(passed_count / total, 4)
            if total
            else 0.0
        ),
        "criteria": {
            name: {
                "passed": sum(values),
                "total": len(values),
                "rate": round(
                    sum(values) / len(values),
                    4,
                ),
            }
            for name, values in sorted(
                counts.items()
            )
        },
        "total_tokens": sum(
            r.get("total_tokens", 0)
            for r in results
        ),
        "estimated_model_cost": round(
            sum(
                r.get("estimated_model_cost", 0)
                for r in results
            ),
            6,
        ),
    }
