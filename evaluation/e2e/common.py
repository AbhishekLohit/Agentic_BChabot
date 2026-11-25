"""Shared helpers for the end-to-end evaluation (validate.py, run.py, score.py).

Strict SQL-result comparison lives here so validation and scoring apply the same rules.
It fixes the false positives of evaluation/prompt_baseline/scoring.result_matches:
row counts must match, every expected value consumes its own cell, and year / month /
day / date columns never count as a matching number for a scalar answer.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
CASES_FILE = HERE / "golden_cases.yaml"
DB_PATH = ROOT / "transactions.db"
DOCS_DIR = ROOT / "data" / "help_documents"
RESULTS_DIR = HERE / "results"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ROUTES = {"BankSQL", "HelpCenter", "Dispute", "Out_of_scope"}
SUBAGENTS = {"sql_generation", "help_center", "dispute_agent", "fallback"}
GUARDRAIL_LABELS = {"SAFE", "GREETING", "OUT_OF_SCOPE", "SYSTEM_VIOLATION", "SQL_VIOLATION", "PII_VIOLATION"}
# Column names that hold a date part (year, month, txn_date, month_name...), not an amount
# such as "monthly_total".
DATE_COLUMN = re.compile(r"^(year|month|day|week|date|time|\w*_(year|month|day|date)|(year|month|date)_\w*)$", re.I)
MONEY_TOL = 0.011


# ----------------------------------------------------------------------------- cases
def load_cases(path: Path = CASES_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


def is_dialogue(case: dict) -> bool:
    """Dispute cases are dialogues driven by a simulated customer (they have an opening)."""
    return case.get("mode") == "dialogue" or "opening" in case


def as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def any_of(expected: Any) -> list:
    """A gold field may be one value or a list of acceptable values."""
    return as_list(expected)


# ----------------------------------------------------------------------------- text
def norm(text: str) -> str:
    """Case-, whitespace- and quote-style-insensitive (same rules as evaluation/rag)."""
    text = (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


_NUM = re.compile(r"(?<![\w.])[-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])")


def numbers_in(text: str) -> list[float]:
    """Numbers written in a reply: '£1,677.09' -> 1677.09, '12' -> 12.0."""
    out = []
    for m in _NUM.finditer((text or "").replace("£", " ").replace("$", " ")):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def number_mentioned(value: float, numbers: Iterable[float]) -> bool:
    """A reply number matches to the penny, or as a whole number rounded from the value."""
    for n in numbers:
        if abs(n - value) <= MONEY_TOL:
            return True
        if float(n).is_integer() and abs(value) >= 100 and abs(n - value) < 0.5:
            return True
    return False


def is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v.replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def to_float(v: Any) -> float:
    return float(str(v).replace(",", "")) if isinstance(v, str) else float(v)


# ----------------------------------------------------------------------------- SQL
def run_sql(sql: str, db_path: Path = DB_PATH) -> tuple[list[str], list[tuple]]:
    """Run read-only; returns (columns, rows). Raises sqlite3.Error."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description] if cur.description else []
        return cols, [tuple(r) for r in cur.fetchmany(500)]
    finally:
        conn.close()


def _cells(columns: list[str], rows: list[tuple], skip_dates: bool) -> list[list[Any]]:
    keep = [i for i, c in enumerate(columns) if not (skip_dates and DATE_COLUMN.search(c or ""))]
    return [[r[i] for i in keep] for r in rows]


def _cell_eq(expected: Any, actual: Any) -> bool:
    if expected is None:
        return actual is None
    if is_number(expected) and is_number(actual):
        return abs(to_float(expected) - to_float(actual)) <= MONEY_TOL
    if isinstance(expected, str) and isinstance(actual, str):
        return norm(expected) == norm(actual)
    return False


def _row_covers(expected_row: list, actual_row: list) -> bool:
    """Every expected cell matched by a DIFFERENT actual cell (extra actual cells allowed)."""
    free = list(range(len(actual_row)))
    for e in expected_row:
        hit = next((i for i in free if _cell_eq(e, actual_row[i])), None)
        if hit is None:
            return False
        free.remove(hit)
    return True


def is_empty_result(rows: list[tuple]) -> bool:
    return not rows or all(all(c is None or c == 0 for c in r) for r in rows)


def compare_results(expected: tuple[list[str], list[tuple]], actual: tuple[list[str], list[tuple]],
                    mode: str) -> tuple[bool, str]:
    """Strict comparison of a predicted query result with the reference result.

    scalar      one row; one non-date cell equals the expected single value
    rows        same row count; each expected row matched to a distinct predicted row
                (columns compared by value, extra predicted columns allowed)
    values      every expected NUMBER appears in a distinct predicted cell (combined
                queries / pivoted layouts); labels and date columns ignored
    last_column same row count; the multiset of last numeric columns is equal
    """
    e_cols, e_rows = expected
    a_cols, a_rows = actual
    if is_empty_result(e_rows):
        return (is_empty_result(a_rows), "expected no data" if not is_empty_result(a_rows) else "")
    if not a_rows:
        return False, "empty result"

    if mode == "scalar":
        value = e_rows[0][0]
        if len(a_rows) != 1:
            return False, f"expected 1 row, got {len(a_rows)}"
        cells = _cells(a_cols, a_rows, skip_dates=True)[0]
        return (any(_cell_eq(value, c) for c in cells), "scalar mismatch")

    if mode == "last_column":
        if len(a_rows) != len(e_rows):
            return False, f"expected {len(e_rows)} rows, got {len(a_rows)}"
        def last_num(row):
            nums = [c for c in row if is_number(c)]
            return round(to_float(nums[-1]), 2) if nums else None
        return (sorted(map(last_num, e_rows), key=str) == sorted(map(last_num, a_rows), key=str),
                "values differ")

    if mode == "rows":
        if len(a_rows) != len(e_rows):
            return False, f"expected {len(e_rows)} rows, got {len(a_rows)}"
        free = list(range(len(a_rows)))
        for er in e_rows:
            hit = next((i for i in free if _row_covers(list(er), list(a_rows[i]))), None)
            if hit is None:
                return False, f"no predicted row matches {er}"
            free.remove(hit)
        return True, ""

    if mode == "values":
        # numbers only: labels may come back as column names in a pivoted / combined query
        wanted = [c for r in _cells(e_cols, e_rows, skip_dates=True) for c in r if is_number(c)]
        pool = [c for r in _cells(a_cols, a_rows, skip_dates=True) for c in r]
        free = list(range(len(pool)))
        for w in wanted:
            hit = next((i for i in free if _cell_eq(w, pool[i])), None)
            if hit is None:
                return False, f"value {w!r} missing"
            free.remove(hit)
        return True, ""

    raise ValueError(f"unknown compare mode {mode!r}")


def result_mentions(columns: list[str], rows: list[tuple]) -> list[Any]:
    """What '$sql' expands to: the non-date values of the reference result."""
    return [c for r in _cells(columns, rows, skip_dates=True) for c in r if c is not None]
