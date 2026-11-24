"""Scoring for the prompt-engineering baseline: no model calls here.

A prediction is {"route": ..., "sql": ...}. For BankSQL questions the predicted SQL is run
read-only on the transactions database and its RESULT is compared with the reference query's
result, so a differently written but equivalent query still counts as correct.

Every miss is given failure labels (FAILURES below), which is how the report documents the
specific ways the model breaks.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Optional

ROUTES = ("BankSQL", "HelpCenter", "Dispute", "Out_of_scope")

# label -> (what it means, how the multi-agent app guards against it)
FAILURES = {
    "api_error": ("The call to the model failed (timeout, rate limit, server error).",
                  "Retries with back-off in the client (max_retries)."),
    "truncated": ("The output was cut off at the token limit (finish_reason = length).",
                  "Short outputs per agent; max_tokens capped at 1,024 on the capstone server."),
    "parse_error": ("No usable JSON / structured answer could be read from the output.",
                    "Schema-enforced structured output; a fallback reply when parsing fails."),
    "thinking_leak": ("Qwen's <think> reasoning text appeared in the answer.",
                      "enable_thinking switched off for the capstone provider."),
    "invalid_route": ("The route is not one of the four allowed labels.",
                      "Literal-typed schema field, so only the four labels can be returned."),
    "wrong_route": ("The question was sent to the wrong place (e.g. a how-to treated as a dispute).",
                    "A dedicated supervisor prompt with contrastive examples per pair of routes."),
    "missing_sql": ("A data question was routed correctly but no SQL was written.",
                    "A separate Text-to-SQL agent whose only job is the query."),
    "unsafe_sql": ("A write / multi-statement SQL was produced (DROP, DELETE, UPDATE, ...).",
                   "Input guardrail (SQL_VIOLATION) and read-only, single-statement execution."),
    "missing_customer_filter": ("The SQL does not restrict rows to the signed-in customer.",
                                "Customer id injected and checked in code, not left to the model."),
    "sql_error_no_such_column": ("The SQL names a column that does not exist.",
                                 "Schema and entity lists in the prompt; one SQL-correction retry."),
    "sql_error_no_such_table": ("The SQL names a table that does not exist.",
                                "Schema in the prompt; SQL-correction retry."),
    "sql_error_syntax": ("The SQL does not parse.", "SQL-correction retry with the error message."),
    "sql_error_other": ("The SQL failed to run for another reason.", "SQL-correction retry."),
    "empty_result": ("The SQL ran but returned nothing / NULL where an answer exists "
                     "(usually a wrong filter value or date).",
                     "Entity extraction of real merchant/category values; dates resolved in code."),
    "wrong_result": ("The SQL ran but gave a different answer (wrong filter, aggregation, "
                     "date range or definition of 'spending').",
                     "Few-shot SQL examples and explicit definitions; evaluation on a frozen set."),
}

_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b", re.I)


# ----------------------------------------------------------------------------- parsing
def parse_text_json(text: str) -> Optional[dict]:
    """The JSON object in a free-text reply (code fences and <think> blocks tolerated)."""
    if not text:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    cleaned = re.sub(r"```(?:json)?", "", cleaned)
    for match in re.finditer(r"\{.*\}", cleaned, re.S):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def clean_sql(sql: Any) -> Optional[str]:
    if sql is None:
        return None
    s = str(sql).strip()
    if not s or s.lower() in ("null", "none"):
        return None
    s = re.sub(r"^```(?:sql)?|```$", "", s, flags=re.I).strip()
    return s.rstrip(";").strip()


def is_unsafe(sql: str) -> bool:
    """Anything but one read-only SELECT / WITH statement."""
    s = sql.strip().rstrip(";")
    if ";" in s:
        return True
    if not re.match(r"^(select|with)\b", s, re.I):
        return True
    return bool(_WRITE.search(s))


def has_customer_filter(sql: str, customer_id: str) -> bool:
    return customer_id.lower() in sql.lower() and "customer_id" in sql.lower()


# ----------------------------------------------------------------------------- execution
def run_sql(db_path: str, sql: str, timeout_s: float = 5.0, max_rows: int = 200):
    """Run read-only; returns (rows, error_label, error_text)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    deadline = time.time() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10_000)
    try:
        rows = conn.execute(sql).fetchmany(max_rows)
        return [tuple(r) for r in rows], None, None
    except sqlite3.Error as e:
        msg = str(e)
        low = msg.lower()
        if "no such column" in low:
            label = "sql_error_no_such_column"
        elif "no such table" in low:
            label = "sql_error_no_such_table"
        elif "syntax" in low or "incomplete input" in low or "unrecognized token" in low:
            label = "sql_error_syntax"
        else:
            label = "sql_error_other"
        return None, label, msg[:200]
    finally:
        conn.close()


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    if isinstance(v, str):
        try:
            return round(float(v.replace(",", "")), 2)
        except ValueError:
            return v.strip().lower()
    return v


def is_empty(rows) -> bool:
    return not rows or all(all(c is None for c in r) for r in rows)


def result_matches(expected: list[tuple], predicted: list[tuple], compare: str = "values") -> bool:
    """Order-insensitive comparison of query results.

    values: every expected row's values must appear in a distinct predicted row (extra
            predicted columns are fine, e.g. a merchant plus its total), same row count.
    last_column: compare only the last column of each row (e.g. totals per month, whatever
            the month is called).
    """
    if not predicted:
        return False
    if compare == "last_column":
        return sorted(_norm(r[-1]) for r in expected) == sorted(_norm(r[-1]) for r in predicted) \
            if len(expected) == len(predicted) else False
    exp = [{_norm(c) for c in r if c is not None} for r in expected]
    pred = [{_norm(c) for c in r if c is not None} for r in predicted]
    if len(exp) == 1:
        return exp[0] <= pred[0]
    if len(exp) != len(pred):
        return False
    unused = list(range(len(pred)))
    for e in exp:
        hit = next((i for i in unused if e <= pred[i]), None)
        if hit is None:
            return False
        unused.remove(hit)
    return True


# ----------------------------------------------------------------------------- scoring
def score(case: dict, pred: Optional[dict], db_path: str, customer_id: str,
          expected_rows: Optional[list] = None, meta: Optional[dict] = None) -> dict:
    """Score one prediction. Returns route_ok, sql_ok, correct, failures and the SQL result."""
    meta = meta or {}
    failures: list[str] = []
    out = {"route_ok": False, "sql_ok": None, "correct": False, "valid_output": False,
           "pred_route": None, "pred_sql": None, "result": None, "sql_error": None}

    if meta.get("api_error"):
        out["failures"] = ["api_error"]
        return out
    if meta.get("finish_reason") == "length":
        failures.append("truncated")
    if "<think>" in (meta.get("raw_text") or ""):
        failures.append("thinking_leak")
    if pred is None:
        out["failures"] = failures + ["parse_error"]
        return out

    route = pred.get("route")
    sql = clean_sql(pred.get("sql"))
    out.update(pred_route=route, pred_sql=sql, valid_output=route in ROUTES)
    if route not in ROUTES:
        failures.append("invalid_route")
    expected_route = case["expected_route"]
    out["route_ok"] = route == expected_route
    if route in ROUTES and not out["route_ok"]:
        failures.append("wrong_route")

    if sql and is_unsafe(sql):
        failures.append("unsafe_sql")              # never executed
        sql_runs = False
    else:
        sql_runs = bool(sql)

    if expected_route == "BankSQL":
        out["sql_ok"] = False
        if not sql:
            if out["route_ok"]:
                failures.append("missing_sql")
        elif sql_runs:
            if not has_customer_filter(sql, customer_id):
                failures.append("missing_customer_filter")
            rows, err_label, err_text = run_sql(db_path, sql)
            out["result"] = [list(r) for r in rows] if rows is not None else None
            out["sql_error"] = err_text
            if err_label:
                failures.append(err_label)
            elif is_empty(rows) and not is_empty(expected_rows):
                failures.append("empty_result")
            elif not result_matches(expected_rows or [], rows, case.get("compare", "values")):
                failures.append("wrong_result")
            else:
                out["sql_ok"] = "missing_customer_filter" not in failures
    out["correct"] = out["route_ok"] and (expected_route != "BankSQL" or bool(out["sql_ok"])) \
        and "unsafe_sql" not in failures
    out["failures"] = failures
    return out


def result_signature(scored: dict) -> str:
    """What a self-consistency vote compares: the route and the query's normalised result."""
    if scored.get("pred_route") != "BankSQL":
        return f"{scored.get('pred_route')}"
    res = scored.get("result")
    if res is None:
        return "BankSQL|error"
    return "BankSQL|" + json.dumps(sorted(str(sorted(str(_norm(c)) for c in r)) for r in res))
