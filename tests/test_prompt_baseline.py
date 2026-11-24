"""Offline tests for eval/prompt_baseline (no model calls)."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation" / "prompt_baseline"))
import run  # noqa: E402
import scoring  # noqa: E402

DB = str(ROOT / "transactions.db")
CASES = {c["id"]: c for c in run.load_cases(ROOT / "evaluation" / "prompt_baseline" / "cases.jsonl")}


class FakeLLM:
    """Returns a fixed answer; structured mode returns the parsed schema object."""
    def __init__(self, answer: dict, text: str | None = None):
        self.answer, self.text = answer, text

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self.text or json.dumps(self.answer), usage_metadata={"total_tokens": 10},
                               response_metadata={"finish_reason": "stop"}, tool_calls=[])

    def with_structured_output(self, schema, include_raw=False):
        outer = self

        class _S:
            async def ainvoke(self, messages):
                raw = SimpleNamespace(content="", usage_metadata={"total_tokens": 20},
                                      response_metadata={"finish_reason": "stop"}, tool_calls=[])
                data = {k: v for k, v in outer.answer.items() if k in schema.model_fields}
                for k in ("reasoning",):
                    if k in schema.model_fields and k not in data:
                        data[k] = "because"
                for k in ("sub_questions", "sub_answers"):
                    if k in schema.model_fields and k not in data:
                        data[k] = ["x"]
                return {"raw": raw, "parsed": schema(**data), "parsing_error": None}
        return _S()


def _args(**kw):
    d = dict(today="2026-11-24", customer="Cust_0410", samples=3, delay=0)
    d.update(kw)
    return SimpleNamespace(**d)


def _run(strategy, case_id, answer, text=None):
    c = CASES[case_id]
    llm = FakeLLM(answer, text)
    return asyncio.run(run.run_case(strategy, c, {"base": llm, "sampling": llm}, _args(), DB, run.expected_rows(DB, c)))


def test_all_reference_sql_non_empty():
    for c in CASES.values():
        if c["expected_route"] == "BankSQL":
            assert not scoring.is_empty(run.expected_rows(DB, c)), c["id"]


def test_prompts_render_without_placeholders():
    for st in run.STRATEGIES:
        assert "{{" not in run.render(run.load_prompt(st), "hi", "2026-11-24", "Cust_0410")


@pytest.mark.parametrize("strategy", run.STRATEGIES)
def test_correct_reference_answer_scores_correct(strategy):
    c = CASES["B21"]
    r = _run(strategy, "B21", {"route": "BankSQL", "sql": c["reference_sql"]})
    assert r["correct"] and r["route_ok"] and r["sql_ok"], r["failures"]


def test_wrong_route_detected():
    r = _run("structured_fewshot", "H06", {"route": "BankSQL", "sql": "SELECT 1"})
    assert not r["correct"] and "wrong_route" in r["failures"]


def test_unsafe_and_missing_filter_detected():
    r = _run("cot", "B01", {"route": "BankSQL", "sql": "DROP TABLE transactions"})
    assert "unsafe_sql" in r["failures"]
    r = _run("cot", "B01", {"route": "BankSQL", "sql": "SELECT SUM(amount) FROM transactions"})
    assert "missing_customer_filter" in r["failures"]


def test_bad_column_and_parse_error():
    r = _run("decomposition", "B01", {"route": "BankSQL",
             "sql": "SELECT nope FROM transactions WHERE customer_id='Cust_0410'"})
    assert "sql_error_no_such_column" in r["failures"]
    r = _run("zero_shot", "B01", {}, text="I think the answer is BankSQL")
    assert "parse_error" in r["failures"] and not r["valid_output"]


def test_self_consistency_reports_agreement():
    r = _run("self_consistency", "O01", {"route": "Out_of_scope", "sql": None})
    assert r["llm_calls"] == 3 and r["agreement"] == 1.0 and r["correct"]


def test_report_written(tmp_path):
    rows = {"cot": [_run("cot", "B01", {"route": "HelpCenter", "sql": None})]}
    meta = {"model": "fake", "n_cases": 1, "today": "2026-11-24", "run_at": "now", "samples": 3}
    text = run.write_report(tmp_path, meta, rows, run.summarize(rows))
    assert "wrong_route" in text and (tmp_path / "failure_modes.md").exists()
