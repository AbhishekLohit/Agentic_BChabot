"""Offline tests for the end-to-end evaluation (evaluation/e2e): strict SQL comparison,
the first-failing-stage taxonomy, the simulated dispute customer and the per-case DB.
No model calls."""
import asyncio
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

import importlib.util

E2E = Path(__file__).resolve().parents[1] / "evaluation" / "e2e"


def _load(name: str, filename: str):
    """Load by path under a unique name: evaluation/prompt_baseline also has a `run.py`
    that its own test imports as `run`, so neither may claim the bare name."""
    spec = importlib.util.spec_from_file_location(name, E2E / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


common = _load("e2e_common", "common.py")
e2e_run = _load("e2e_run", "run.py")
score = _load("e2e_score", "score.py")

TODAY = date(2026, 11, 25)


# ----------------------------------------------------------------------------- strict matcher
def test_count_does_not_match_a_month_column():
    # the Week 1 matcher accepted this: expected count 12 vs (year, month, total)
    ok, _ = common.compare_results((["n"], [(12,)]), (["year", "month", "total"], [(2026, 12, 3456.78)]), "scalar")
    assert not ok


def test_scalar_needs_exactly_one_row():
    ok, why = common.compare_results((["t"], [(100.0,)]), (["t", "m"], [(100.0, "x"), (5.0, "y"), (7.0, "z")]), "scalar")
    assert not ok and "1 row" in why


def test_scalar_accepts_extra_label_column():
    assert common.compare_results((["t"], [(5933.24,)]), (["category", "total_spent"], [("all", 5933.24)]), "scalar")[0]


def test_monthly_total_alias_is_not_treated_as_a_date():
    assert common.compare_results((["t"], [(10.0,)]), (["monthly_total"], [(10.0,)]), "scalar")[0]


def test_rows_must_keep_values_together():
    expected = (["c", "t"], [("tesco", 50.0), ("aldi", 20.0)])
    assert not common.compare_results(expected, (["c", "t"], [("tesco", 20.0), ("aldi", 50.0)]), "rows")[0]
    assert common.compare_results(expected, (["c", "t"], [("Aldi", 20.0), ("TESCO", 50.0)]), "rows")[0]


def test_values_mode_for_combined_queries():
    exp = (["income"], [(7684.54,)])
    assert common.compare_results(exp, (["income", "spend"], [(7684.54, 5933.24)]), "values")[0]
    assert not common.compare_results(exp, (["income", "spend"], [(1.0, 5933.24)]), "values")[0]


def test_empty_reference_expects_no_data():
    assert common.compare_results((["t"], [(None,)]), (["t"], [(None,)]), "scalar")[0]
    assert not common.compare_results((["t"], [(None,)]), (["t"], [(12.5,)]), "scalar")[0]


def test_numbers_in_reply():
    nums = common.numbers_in("You spent £1,677.09 over 8 visits (avg £209.64).")
    assert 1677.09 in nums and 8.0 in nums and 209.64 in nums
    assert common.number_mentioned(1677.09, [1677.0])        # rounded to pounds is fine above £100
    assert not common.number_mentioned(86.36, [86.0])          # but not for small amounts


# ----------------------------------------------------------------------------- taxonomy
REF = "SELECT ROUND(SUM(amount_usd),2) FROM transactions WHERE customer_id='Cust_0410' AND transaction_type!='income' AND year=2026 AND month=10"


def sql_expect():
    return {"guardrail": "SAFE", "routes": ["BankSQL"], "subagents": ["sql_generation"],
            "context": [{"must_keep": [["october 2026"]]}],
            "sql": [{"reference": REF, "compare": "scalar"}],
            "response": {"behavior": "answer", "must_mention": ["$sql"]}}


def sql_state(value, reply_value=None, **over):
    st = {
        "fallback_response": None,
        "context_builder_response": {"contextual_queries": ["How much did I spend in October 2026?"]},
        "suprivisor_response": {"classified_sub_queries": [{"class_label": "BankSQL"}]},
        "text2sql_generator_response": {"sql_query": REF},
        "database_query_results": [{"total": value}],
        "agent_metrics": {"guardrail": {"total_tokens": 10}},
    }
    st.update(over)
    return st


def turn(state, reply):
    return {"turn": 0, "user": "How much did I spend in October 2026?", "reply": reply, "state": state}


def run_score(state, reply, exp=None, case=None):
    case = case or {"id": "T", "chat_history": []}
    return asyncio.run(score.score_turn(case, turn(state, reply), exp or sql_expect(), None, TODAY, {}))


def ref_value():
    return common.run_sql(REF)[1][0][0]


def test_correct_turn():
    v = ref_value()
    out = run_score(sql_state(v), f"You spent £{v:,.2f} in October 2026.")
    assert out["e2e_correct"] and out["failure"] is None


def test_wrong_sql_result_is_reasoning_error():
    out = run_score(sql_state(1.23), "You spent £1.23 in October 2026.")
    assert out["failure"]["bucket"] == "reasoning_error" and out["failure"]["stage"] == "sql_result"


def test_sql_failure_is_tool_call_error():
    st = sql_state(None, database_query_results=None,
                   fallback_response={"fallback_node": "sql_execution", "fallback_reason": "query_error",
                                      "fallback_message": "failed"})
    out = run_score(st, "Sorry, something went wrong.")
    assert out["failure"]["bucket"] == "tool_call_error"


def test_exception_is_tool_call_error():
    out = asyncio.run(score.score_turn({"id": "T"}, {"turn": 0, "user": "x", "reply": "", "state": {},
                                                     "error": "TimeoutError: api"}, sql_expect(), None, TODAY, {}))
    assert out["failure"] == {"bucket": "tool_call_error", "stage": "error", "detail": "TimeoutError: api",
                              "cause": "api_timeout"}


def test_logged_rate_limit_is_labelled_as_load():
    st = {"fallback_response": {"fallback_node": "fallback_agent", "fallback_reason": "exception"}}
    rec = {"turn": 0, "user": "x", "reply": "technical issue", "state": st,
           "logged_errors": ["Error in guardrail agent: Error code: 429 - per-key concurrent request limit exceeded"]}
    out = asyncio.run(score.score_turn({"id": "T"}, rec, sql_expect(), None, TODAY, {}))
    assert out["failure"]["cause"] == "api_rate_limit"


def test_dispute_error_status_is_a_tool_error():
    exp = {"dispute": {"status": "needs_payment_confirmation"}, "response": {"behavior": "answer"}}
    st = {"dispute_result": {"status": "error", "data": {}}}
    rec = {"turn": 0, "user": "x", "reply": "technical issue", "state": st,
           "logged_errors": ["Error in disputes agent | openai.LengthFinishReasonError: length limit was reached"]}
    out = asyncio.run(score.score_turn({"id": "T"}, rec, exp, None, TODAY, {}))
    assert out["failure"]["bucket"] == "tool_call_error" and out["failure"]["cause"] == "output_truncated"


def test_invented_amount_with_right_sql_is_hallucination():
    v = ref_value()
    out = run_score(sql_state(v), f"You spent £{v:,.2f} in October, including £999.99 at Tesco.")
    assert out["failure"]["bucket"] == "hallucination"


def test_wrong_guardrail_comes_first():
    st = sql_state(1.0, fallback_response={"fallback_node": "guardrail", "fallback_reason": "PII_VIOLATION"})
    out = run_score(st, "I can't help with that.")
    assert out["failure"]["stage"] == "guardrail" and out["failure"]["bucket"] == "reasoning_error"


def test_missed_evidence_is_retrieval_miss():
    exp = {"guardrail": "SAFE", "routes": ["HelpCenter"], "subagents": ["help_center"],
           "rag": {"answerable": True, "evidence": [{"doc": "kb_0315_x.md", "quote": "click on 'Forgot MPIN'"}]},
           "response": {"behavior": "answer", "must_mention": [["forgot mpin"]]}}
    st = {"suprivisor_response": {"classified_sub_queries": [{"class_label": "HelpCenter"}]},
          "help_source": "help_documents", "help_contexts": [{"n": 1, "text": "Something unrelated."}]}
    out = run_score(st, "I couldn't find that in the help documents.", exp)
    assert out["failure"]["bucket"] == "retrieval_miss"


def test_refusing_when_it_should_is_correct():
    exp = {"guardrail": ["SYSTEM_VIOLATION"], "subagents": [], "response": {"behavior": "refuse"}}
    st = {"fallback_response": {"fallback_node": "guardrail", "fallback_reason": "SYSTEM_VIOLATION"}}
    assert run_score(st, "I can only help with your banking.", exp)["e2e_correct"]


# ----------------------------------------------------------------------------- simulated customer
def result(status, **data):
    return SimpleNamespace(status=status, data=data)


FACTS = {"merchant": "Wayfair", "amount": "£76.61", "date": "31 October 2026", "choose": "The £12.63 one.",
         "answers": {"order_date": "On 31 October.", "promised_delivery": "7 November."},
         "replies": {"needs_payment_confirmation": ["No, it was £10.56.", "Yes."]}}


def test_simulator_answers_what_was_asked():
    used = {}
    assert "Wayfair" in e2e_run.simulate_reply(FACTS, result("needs_transaction", missing=["date"]), used)
    assert e2e_run.simulate_reply(FACTS, result("needs_info", slot_ids=["order_date", "promised_delivery"]), used) \
        == "On 31 October. 7 November."
    assert e2e_run.simulate_reply(FACTS, result("needs_transaction_choice"), used) == "The £12.63 one."
    assert e2e_run.simulate_reply(FACTS, result("needs_payment_confirmation"), used).startswith("No")
    assert e2e_run.simulate_reply(FACTS, result("needs_payment_confirmation"), used) == "Yes."
    assert e2e_run.simulate_reply(FACTS, result("filed"), used) is None


# ----------------------------------------------------------------------------- per-case DB + task
def test_prepare_db_is_clean_and_seeded(tmp_path):
    case = {"id": "X", "setup": {"existing_disputes": [{"transaction_id": "TX_9fd609e3", "reason": "goods_not_received"}]}}
    conn = e2e_run.prepare_db(case, tmp_path)
    out = e2e_run.db_outcome(conn, 1)
    conn.close()
    assert len(out["disputes"]) == 1 and out["new_disputes"] == []
    assert out["transactions_count"] == common.run_sql("SELECT COUNT(*) FROM transactions")[1][0][0]


def test_dispute_task_checked_in_the_db():
    case = {"task": {"type": "dispute_case_raised", "transaction_id": ["TX_1", "TX_2"], "reason": "duplicate_charge"}}
    run = {"turns": [{"reply": "Case raised"}], "dispute_statuses": ["filed"],
           "db": {"new_disputes": [{"case_id": "DSP-1", "transaction_id": "TX_2", "reason": "duplicate_charge"}]}}
    assert score.task_success(case, run, [], 0)[0]
    run["db"]["new_disputes"][0]["reason"] = "wrong_amount"
    assert not score.task_success(case, run, [], 0)[0]


def test_golden_set_loads_and_has_80_cases():
    data = common.load_cases()
    assert len(data["cases"]) == 80
    assert len({c["id"] for c in data["cases"]}) == 80
