"""Deterministic scoring of LangGraph states (no LLM judge or credentials needed)."""
from __future__ import annotations

import ast
import math
import re
import sqlite3
from collections import Counter
from typing import Any

VALID_ROUTES = {"BankSQL", "HelpCenter", "Out_of_scope", "guardrail"}


def field(obj: Any, name: str, default: Any = None) -> Any:
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


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


def parse_result_values(items: list[str] | None) -> list[Any]:
    """Parse SQL executor's stringified dictionaries; never evaluate code."""
    rows = []
    for item in items or []:
        try:
            row = ast.literal_eval(item)
        except (ValueError, SyntaxError, TypeError, MemoryError):
            return []
        if not isinstance(row, dict):
            return []
        rows.append(list(row.values()))
    return rows


def _canonical(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 5) if math.isfinite(float(value)) else str(value)
    return str(value).strip().casefold() if value is not None else None


def compare_results(actual: list[list[Any]], expected: list[list[Any]], *, unordered: bool = False) -> bool:
    """Compare result values independently of SQL column aliases."""
    convert = lambda rows: [tuple(_canonical(v) for v in row) for row in rows]
    lhs, rhs = convert(actual), convert(expected)
    return Counter(lhs) == Counter(rhs) if unordered else lhs == rhs


def expected_sql_values(conn: sqlite3.Connection, query: str) -> list[list[Any]]:
    """Execute a trusted local reference SELECT against a read-only database."""
    cleaned = query.strip().rstrip(";").strip()
    if not re.match(r"^(SELECT|WITH)\b", cleaned, re.IGNORECASE) or ";" in cleaned:
        raise ValueError("Reference SQL must be a single SELECT or WITH query")
    cursor = conn.execute(cleaned)
    if cursor.description is None:
        raise ValueError("Reference SQL returned no columns")
    return [list(row) for row in cursor.fetchall()]


def score_case(case: dict, state: dict, *, conn: sqlite3.Connection | None = None) -> dict:
    """Return criterion-level results; non-applicable criteria are not counted."""
    checks: dict[str, bool] = {}
    expected_route = set(case.get("expected_routes", []))
    if expected_route:
        checks["routing"] = classify_routes(state) == expected_route

    sql_resp = state.get("text2sql_generator_response")
    sql = field(sql_resp, "sql_query", "") or ""
    if case.get("expected_sql"):
        if conn is None:
            raise ValueError("DB connection required to score expected_sql")
        expected = expected_sql_values(conn, case["expected_sql"])
        actual = parse_result_values(state.get("database_query_results"))
        checks["sql_result"] = compare_results(actual, expected, unordered=case.get("unordered", False))
        # This is a defense-in-depth signal, not proof that SQL is authorized.
        checks["sql_read_only"] = bool(re.match(r"^\s*(SELECT|WITH)\b", sql, re.IGNORECASE))

    answer = field(state.get("response_generation_response"), "generated_response", "") or ""
    if case.get("answer_contains"):
        checks["answer_keywords"] = all(
            term.casefold() in answer.casefold() for term in case["answer_contains"]
        )
    if case.get("answer_excludes"):
        checks["answer_excludes"] = all(
            term.casefold() not in answer.casefold() for term in case["answer_excludes"]
        )
    fallback = state.get("fallback_response")
    if "expected_fallback" in case:
        checks["fallback"] = bool(field(fallback, "fallback_flag", False)) == case["expected_fallback"]
    metrics = state.get("agent_metrics") or {}
    total_tokens = sum(int(field(m, "total_tokens", 0) or 0) for m in metrics.values())
    cost = sum(float(field(m, "model_cost_with_cache", 0) or 0) for m in metrics.values())
    return {
        "id": case["id"],
        "category": case["category"],
        "checks": checks,
        "passed": bool(checks) and all(checks.values()),
        "total_tokens": total_tokens,
        "estimated_model_cost": round(cost, 6),
        "routes_observed": sorted(classify_routes(state)),
        "has_sql": bool(sql),
    }


def summarize(results: list[dict]) -> dict:
    total = len(results)
    counts: dict[str, list[bool]] = {}
    for result in results:
        for name, passed in result.get("checks", {}).items():
            counts.setdefault(name, []).append(passed)
    return {
        "total": total,
        "passed": sum(r["passed"] for r in results),
        "pass_rate": round(sum(r["passed"] for r in results) / total, 4) if total else 0.0,
        "criteria": {
            name: {"passed": sum(values), "total": len(values), "rate": round(sum(values) / len(values), 4)}
            for name, values in sorted(counts.items())
        },
        "total_tokens": sum(r.get("total_tokens", 0) for r in results),
        "estimated_model_cost": round(sum(r.get("estimated_model_cost", 0) for r in results), 6),
    }
