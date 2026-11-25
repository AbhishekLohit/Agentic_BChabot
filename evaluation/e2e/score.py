"""Score an end-to-end run against the golden set. Re-runnable: no chat-model calls
(the optional Claude judge is cached in the run folder).

Per scored turn, every stage is checked in pipeline order:
    error -> guardrail -> dispute_gate -> context -> routes -> subagents
          -> sql | rag (retrieval) | dispute -> response
A turn is end-to-end correct when its final reply is right (response check, plus the
dispute decision / safety checks where the case has them). When it is not, the failure is
put in ONE bucket by its FIRST failing stage:
    tool_call_error  an exception, step limit, SQL that fails or is never written, unsafe SQL
    retrieval_miss   gold help evidence not retrieved, or the dispute lookup missed the payment
    reasoning_error  wrong guardrail / gate / rewrite / route / sub-agent, SQL that runs but
                     answers wrongly, a wrong dispute decision, or a reply missing key facts
    hallucination    every earlier stage right, but the reply states unsupported facts or
                     answers when it should have abstained
Stages that failed on a turn that still ended correct are reported as "recovered".

Usage:
    python evaluation/e2e/score.py evaluation/e2e/results/<run>            # deterministic checks
    python evaluation/e2e/score.py evaluation/e2e/results/<run> --judge    # + Claude judge
Writes scored.jsonl, summary.json and report.md into the run folder.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CASES_FILE, DB_PATH, HERE, ROOT, as_list, compare_results, is_dialogue,  # noqa: E402
                    load_cases, norm, number_mentioned, numbers_in, result_mentions, run_sql)

from dotenv import load_dotenv  # noqa: E402
from pydantic import BaseModel  # noqa: E402

load_dotenv(ROOT / ".env")

JUDGE_MODEL = "claude-sonnet-5-5"
BUCKETS = ["tool_call_error", "retrieval_miss", "reasoning_error", "hallucination"]
STAGE_BUCKET = {
    "error": "tool_call_error", "guardrail": "reasoning_error", "dispute_gate": "reasoning_error",
    "context": "reasoning_error", "routes": "reasoning_error", "subagents": "reasoning_error",
    "sql_tool": "tool_call_error", "sql_result": "reasoning_error", "safety": "reasoning_error",
    "rag_retrieval": "retrieval_miss", "dispute_lookup": "retrieval_miss", "dispute": "reasoning_error",
    "response_unsupported": "hallucination", "response_incomplete": "reasoning_error",
}
STAGE_ORDER = ["error", "guardrail", "dispute_gate", "context", "routes", "subagents", "safety",
               "sql_tool", "sql_result", "rag_retrieval", "dispute_lookup", "dispute",
               "response_unsupported", "response_incomplete"]
REFUSAL_HINTS = ("can't", "cannot", "can not", "unable", "not able", "only help", "only assist",
                 "i'm configured", "i am configured", "not something i", "outside", "don't have",
                 "do not have", "couldn't find", "could not find", "not available", "not provide",
                 "no information", "not enough information", "sorry", "not allowed", "won't be able")
MONEY = re.compile(r"£\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*\.\d{1,2}\b|\b\d+(?:\.\d+)?\s?%")


# ----------------------------------------------------------------------------- state access
def dig(obj: Any, *path: str) -> Any:
    for p in path:
        if obj is None:
            return None
        obj = obj.get(p) if isinstance(obj, dict) else getattr(obj, p, None)
    return obj


def guardrail_label(state: dict) -> str:
    fb = state.get("fallback_response") or {}
    if fb.get("fallback_node") == "guardrail":
        reason = fb.get("fallback_reason") or ""
        return "GREETING" if reason == "greeting" else ("ERROR" if reason == "exception" else reason.upper())
    return "SAFE"


def ran_subagents(state: dict) -> set[str]:
    ran = set()
    if state.get("text2sql_generator_response") is not None or state.get("database_query_results") is not None:
        ran.add("sql_generation")
    if state.get("help_source"):
        ran.add("help_center")
    if state.get("dispute_result") is not None:
        ran.add("dispute_agent")
    return ran


def routes_of(state: dict) -> list[str]:
    subs = dig(state, "suprivisor_response", "classified_sub_queries") or []
    return [s.get("class_label") for s in subs]


def dispute_txn(result: dict | None) -> str | None:
    data = (result or {}).get("data") or {}
    return data.get("transaction_id") or dig(data, "transaction", "transaction_id")


# ----------------------------------------------------------------------------- helpers
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h), 3), round(min(1, c + h), 3))


def pct(k: int, n: int) -> str:
    if not n:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.0f}% (95% CI {100 * lo:.0f}-{100 * hi:.0f})"


def contains_any(text: str, options: Any) -> bool:
    t = norm(text)
    return any(norm(str(o)) in t for o in as_list(options))


def expand_tokens(options: list, today: date) -> list[str]:
    out = []
    for o in options:
        if o == "$last_month":
            last = (today.replace(day=1) - timedelta(days=1))
            out += [last.strftime("%B %Y"), last.strftime("%Y-%m"), last.strftime("%b %Y"), "last month"]
        elif o == "$minus_30_days":
            d = today - timedelta(days=30)
            out += ["30 days", d.isoformat(), d.strftime("%d %B %Y").lstrip("0")]
        elif o == "$this_year":
            out += [str(today.year), "this year"]
        else:
            out.append(str(o))
    return out


def is_refusal(reply: str, state: dict) -> bool:
    if guardrail_label(state) not in ("SAFE", "GREETING"):
        return True
    rep = state.get("help_citation_report") or {}
    if rep.get("refused") or rep.get("abstained"):
        return True
    r = norm(reply)
    return any(h in r for h in REFUSAL_HINTS)


def allowed_numbers(case: dict, rec: dict, state: dict, refs: list[tuple]) -> list[float]:
    """Numbers the reply may legitimately contain: from the conversation, the SQL rows the
    app used, the reference results, the help excerpts and the dispute data."""
    texts = [m.get("content", "") for m in case.get("chat_history") or []]
    texts += [rec["user"], json.dumps(state.get("database_query_results") or [], default=str)]
    texts += [c.get("text", "") for c in state.get("help_contexts") or []]
    texts += [json.dumps(state.get("dispute_result") or {}, default=str)]
    texts += [json.dumps(case.get("facts") or {}, default=str)]
    for cols, rows in refs:
        texts.append(json.dumps(rows, default=str))
    nums = []
    for t in texts:
        nums += numbers_in(t)
    return nums


def unsupported_money(reply: str, allowed: list[float]) -> list[str]:
    bad = []
    for m in MONEY.finditer(reply or ""):
        vals = numbers_in(m.group(0))
        if vals and not number_mentioned(vals[0], allowed):
            bad.append(m.group(0))
    return bad


# ----------------------------------------------------------------------------- judge
class FactCheck(BaseModel):
    fact: str
    present: bool


class JudgeVerdict(BaseModel):
    key_facts: list[FactCheck]
    unsupported_claims: list[str]
    answers_the_question: bool
    verdict: Literal["correct", "incomplete", "incorrect"]
    reason: str


# `from __future__ import annotations` leaves these as strings; resolve them now, or the
# Anthropic SDK's parser fails with "not fully defined" on every judge call.
FactCheck.model_rebuild()
JudgeVerdict.model_rebuild()


class Judge:
    """Claude grades only what code can't: paraphrased facts and unsupported claims.
    Results are cached in the run folder, so re-scoring is free and repeatable."""

    def __init__(self, run_dir: Path, enabled: bool):
        self.enabled = enabled
        self.path = run_dir / "judge_cache.json"
        self.cache = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.prompt = None
        self.client = None
        self.calls = 0
        self.errors: list[str] = []
        if enabled:
            import anthropic
            import yaml
            self.client = anthropic.AsyncAnthropic()
            self.prompt = yaml.safe_load((HERE / "judge_prompt.yml").read_text(encoding="utf-8"))["prompt"]

    async def grade(self, question: str, evidence: str, key_facts: list[str], reply: str) -> dict | None:
        payload = json.dumps([question, evidence, key_facts, reply], ensure_ascii=False)
        prompt_text = self.prompt or (HERE / "judge_prompt.yml").read_text(encoding="utf-8")
        key = hashlib.sha256((JUDGE_MODEL + prompt_text + payload).encode()).hexdigest()[:24]
        if key in self.cache:
            return self.cache[key]
        if not self.enabled:
            return None
        user = (f"QUESTION (with earlier conversation):\n{question}\n\nEVIDENCE:\n{evidence or '(none)'}\n\n"
                f"KEY FACTS:\n" + ("\n".join(f"- {f}" for f in key_facts) or "(none)") + f"\n\nREPLY:\n{reply}")
        try:
            resp = await self.client.messages.parse(
                model=JUDGE_MODEL, max_tokens=4000, system=self.prompt,
                messages=[{"role": "user", "content": user}], output_format=JudgeVerdict)
            self.calls += 1
            if resp.stop_reason == "refusal" or resp.parsed_output is None:
                out = {"error": f"judge returned no verdict (stop_reason={resp.stop_reason})"}
            else:
                out = resp.parsed_output.model_dump()
        except Exception as e:  # keep scoring, but count it: the report must say so
            self.errors.append(f"{type(e).__name__}: {str(e)[:200]}")
            return {"error": self.errors[-1]}
        self.cache[key] = out
        return out

    def save(self):
        if self.cache:
            self.path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")


def error_cause(text: str) -> str:
    """Sub-label for tool-call errors: the shared API's rate limit is load, not app quality."""
    t = text.lower()
    if "429" in t or "ratelimit" in t or "rate limit" in t or "concurrent request limit" in t:
        return "api_rate_limit"
    if "lengthfinishreason" in t or "length limit" in t:
        return "output_truncated"
    if "timeout" in t:
        return "api_timeout"
    if "step_limit" in t:
        return "step_limit"
    if "sql" in t:
        return "sql"
    return "other"


def tool_calls_of(state: dict) -> int:
    """Non-LLM tool steps in one turn: SQL runs (a correction re-runs it), a help-document
    retrieval, and a dispute workflow step (database lookups / writes)."""
    agents = [t.get("agent") for t in state.get("agent_trace") or []]
    return (agents.count("sql_execution") + agents.count("sql_correction")
            + (1 if state.get("help_source") else 0) + (1 if state.get("dispute_result") else 0))


# ----------------------------------------------------------------------------- one turn
async def score_turn(case: dict, rec: dict, exp: dict, judge: Judge, today: date,
                     ref_cache: dict) -> dict:
    state = rec.get("state") or {}
    reply = rec.get("reply") or ""
    st: dict[str, dict] = {}

    def put(name: str, ok: bool | None, detail: Any = None):
        st[name] = {"ok": ok, "detail": detail}

    # error / tool failure anywhere in the graph
    fb = state.get("fallback_response") or {}
    logged = rec.get("logged_errors") or []
    err = rec.get("error")
    if not err and fb.get("fallback_reason") == "exception":
        err = f"exception in {fb.get('fallback_node')}"
    if not err and (state.get("dispute_result") or {}).get("status") == "error":
        err = "exception in the dispute workflow"
    if err and logged:
        err = f"{err}: {logged[0]}"
    put("error", err is None, err)

    # guardrail
    label = guardrail_label(state)
    want_guard = as_list(exp.get("guardrail"))
    if want_guard:
        put("guardrail", label in want_guard, {"got": label, "want": want_guard})
    blocked = label not in ("SAFE", "ERROR")

    # dispute gate
    if "dispute_gate" in exp:
        got = state.get("dispute_gate_kind")
        put("dispute_gate", got in as_list(exp["dispute_gate"]), {"got": got, "want": exp["dispute_gate"]})

    # context builder: count + entities/timeframes kept (a spec, not exact text)
    queries = dig(state, "context_builder_response", "contextual_queries") or []
    specs = exp.get("context") or []
    if (specs or exp.get("context_count")) and not blocked:
        counts = as_list(exp.get("context_count")) or ([len(specs)] if specs else [])
        problems = []
        if counts and len(queries) not in counts:
            problems.append(f"{len(queries)} sub-queries, want {counts}")
        joined = " | ".join(queries)
        for spec in specs:
            for group in spec.get("must_keep", []):
                if not contains_any(joined, expand_tokens(as_list(group), today)):
                    problems.append(f"lost {as_list(group)[0]!r}")
            for bad in spec.get("must_not", []):
                if contains_any(joined, bad):
                    problems.append(f"kept {bad!r}")
        put("context", not problems, {"queries": queries, "problems": problems})

    # routes (supervisor) and sub-agents actually run
    want_routes = exp.get("routes")
    if want_routes and not blocked and state.get("suprivisor_response") is not None:
        alts = want_routes if any(isinstance(x, list) for x in want_routes) else [want_routes]
        got = routes_of(state)
        put("routes", any(set(got) == set(a) for a in alts), {"got": got, "want": alts})
    elif want_routes and not blocked and "Dispute" not in sum([as_list(x) for x in want_routes], []) \
            and state.get("dispute_gate_kind") is None:
        put("routes", False, {"got": "supervisor did not run", "want": want_routes})
    if "subagents" in exp:
        got = ran_subagents(state)
        put("subagents", got == set(exp["subagents"]), {"got": sorted(got), "want": exp["subagents"]})

    # safety: the generated SQL must stay on the signed-in customer
    sql_text = dig(state, "text2sql_generator_response", "sql_query") or ""
    if exp.get("sql_must_filter_customer") and sql_text:
        others = re.findall(r"cust_\d+", sql_text.lower())
        ok = "customer_id" in sql_text.lower() and all(o == "cust_0410" for o in others)
        put("safety", ok, sql_text)

    # SQL: tool step (written + ran) then result (strictly compared)
    refs = []
    for ref in exp.get("sql") or []:
        if ref["reference"] not in ref_cache:
            ref_cache[ref["reference"]] = run_sql(ref["reference"])
        refs.append((ref, ref_cache[ref["reference"]]))
    if refs and not blocked:
        rows = state.get("database_query_results")
        sql_fail = None
        if not sql_text:
            sql_fail = "no SQL written" if fb.get("fallback_reason") != "needs_separate_questions" else None
        elif fb.get("fallback_node") in ("sql_execution", "sql_correction") or fb.get("fallback_reason") == "query_error":
            sql_fail = f"SQL failed: {fb.get('fallback_message')}"
        elif re.search(r"\b(insert|update|delete|drop|alter|create|attach|pragma)\b", sql_text, re.I):
            sql_fail = "unsafe SQL"
        if "sql_generation" in ran_subagents(state) or sql_fail:
            put("sql_tool", sql_fail is None, {"sql": sql_text, "error": sql_fail,
                                               "corrected": state.get("sql_query_correction")})
        if sql_fail is None and (rows is not None or fb.get("fallback_reason") == "needs_separate_questions"):
            cols = list(rows[0].keys()) if rows else []
            actual = (cols, [tuple(r.values()) for r in rows or []])
            results = []
            for ref, expected in refs:
                mode = ref.get("compare", "values") if len(refs) == 1 else "values"
                ok, why = compare_results(expected, actual, mode)
                results.append({"ok": ok, "why": why, "expected": expected[1][:5]})
            put("sql_result", all(r["ok"] for r in results),
                {"sql": sql_text, "rows": [list(r) for r in actual[1][:8]], "checks": results})

    # RAG retrieval: was the gold evidence among the excerpts the answer was written from?
    rag = exp.get("rag") or {}
    if rag.get("answerable") is True and "help_center" in ran_subagents(state):
        ctxs = state.get("help_contexts") or []
        texts = [norm(c.get("text", "")) for c in ctxs]
        hit = [ev["doc"] for ev in rag.get("evidence", []) if any(norm(ev["quote"]) in t for t in texts)]
        put("rag_retrieval", bool(hit), {"hit": hit, "retrieved": [c.get("source") for c in ctxs]})

    # dispute decision (and whether the payment lookup found the right one)
    dres = state.get("dispute_result")
    if exp.get("dispute"):
        want = exp["dispute"]
        got_status = (dres or {}).get("status")
        got_txn = dispute_txn(dres)
        want_txn = as_list(want.get("transaction_id"))
        if want_txn and got_status in ("needs_payment_confirmation",) and got_txn not in want_txn:
            put("dispute_lookup", False, {"got_txn": got_txn, "want_txn": want_txn})
        elif want_txn and got_status == "needs_transaction" and ((dres or {}).get("data") or {}).get("not_found"):
            put("dispute_lookup", False, {"got": "not found", "want_txn": want_txn})
        ok = got_status in as_list(want.get("status")) and (not want_txn or got_txn in want_txn or
                                                            got_status not in ("needs_payment_confirmation",))
        put("dispute", ok, {"got": got_status, "txn": got_txn, "want": want})
    if exp.get("no_dispute_started"):
        put("dispute", dres is None, {"got": (dres or {}).get("status")})

    # response: behaviour, must-mention / must-not, unsupported money, then the judge
    resp = exp.get("response") or {}
    behaviors = as_list(resp.get("behavior"))
    incomplete, unsupported = [], []
    refused = is_refusal(reply, state)  # broad: used to confirm a decline / abstention
    # For replies that should ANSWER, only strong signals count as a refusal ("No, you can't
    # get it by SMS" is an answer, not a refusal).
    strong_refusal = (label not in ("SAFE", "GREETING")
                      or bool((state.get("help_citation_report") or {}).get("refused"))
                      or bool((state.get("help_citation_report") or {}).get("abstained"))
                      or norm(reply).startswith("i couldn't find information about that in the available help"))
    chart_made = bool(dig(state, "visualization_response", "decision", "should_visualize")
                      and dig(state, "visualization_response", "plotly_json"))
    if not reply and "greet" not in behaviors and not ("chart" in behaviors and chart_made):
        incomplete.append("empty reply")
    if "answer" in behaviors and "abstain" not in behaviors and "refuse" not in behaviors and strong_refusal \
            and not any(r for r in refs):
        incomplete.append("refused or abstained instead of answering")
    if set(behaviors) & {"refuse", "decline", "abstain"} and not set(behaviors) & {"answer"}:
        if not refused:
            unsupported.append("answered instead of declining/abstaining")
    if "greet" in behaviors and label != "GREETING":
        incomplete.append(f"not treated as a greeting ({label})")
    if "chart" in behaviors:
        viz = state.get("visualization_response") or {}
        should = dig(viz, "decision", "should_visualize")
        ctype = (dig(viz, "decision", "chart_type") or "").lower()
        if not should or not viz.get("plotly_json"):
            incomplete.append("no chart produced")
        elif resp.get("chart_type") and ctype not in [c.lower() for c in resp["chart_type"]]:
            incomplete.append(f"chart type {ctype}, want {resp['chart_type']}")
    if "ask_details" in behaviors and dres is None:
        incomplete.append("did not ask for the dispute details")
    for item in resp.get("must_mention") or []:
        if item == "$sql":
            nums = numbers_in(reply)
            for _, (cols, rows) in refs:
                for v in result_mentions(cols, rows):
                    if isinstance(v, (int, float)) and not number_mentioned(float(v), nums):
                        incomplete.append(f"missing {v}")
                    elif isinstance(v, str) and norm(v) not in norm(reply):
                        incomplete.append(f"missing {v!r}")
        elif not contains_any(reply, item):
            incomplete.append(f"missing {as_list(item)[0]!r}")
    for bad in resp.get("must_not") or []:
        if contains_any(reply, bad):
            unsupported.append(f"contains {bad!r}")
    allowed = allowed_numbers(case, rec, state, [r[1] for r in refs])
    if resp.get("must_not_numbers"):
        unsupported += [f"number {m}" for m in unsupported_money(reply, [])]
    else:
        unsupported += [f"unsupported amount {m}" for m in unsupported_money(reply, allowed)]

    verdict = None
    if judge and reply and set(behaviors) & {"answer", "no_data"} and not blocked:
        evidence = []
        if state.get("database_query_results") is not None:
            evidence.append("DATABASE ROWS:\n" + json.dumps(state["database_query_results"][:20], default=str))
        for c in state.get("help_contexts") or []:
            evidence.append(f"[{c.get('n')}] ({c.get('source')})\n{c.get('text')}")
        for ref, (cols, rows) in refs:
            evidence.append(f"REFERENCE RESULT (correct answer data): columns={cols} rows={rows[:20]}")
        if dres:
            evidence.append("DISPUTE DATA (what the dispute workflow found and decided): "
                            + json.dumps({k: dres.get(k) for k in ("status", "summary", "data")}, default=str)[:3000])
        if rag.get("optional_facts"):
            evidence.append("OPTIONAL FACTS (context only, not required): " + "; ".join(rag["optional_facts"]))
        history = "\n".join(f"{m['role']}: {m['content']}" for m in case.get("chat_history") or [])
        question = (f"(Today's date when this was asked: {today.isoformat()})\n"
                    + (history + "\n" if history else "") + f"user: {rec['user']}")
        verdict = await judge.grade(question, "\n\n".join(evidence), rag.get("key_facts") or [], reply)
        if verdict and not verdict.get("error"):
            missing = [f["fact"] for f in verdict["key_facts"] if not f["present"]]
            incomplete += [f"judge: missing fact '{m}'" for m in missing]
            unsupported += [f"judge: {c}" for c in verdict["unsupported_claims"]]
            if not verdict["answers_the_question"]:
                incomplete.append("judge: does not answer the question")
    dropped = (state.get("help_citation_report") or {}).get("dropped_sentences") or []
    if incomplete and dropped:  # diagnosis: the model wrote it, the strict citation check removed it
        incomplete.append(f"citation check removed {len(dropped)} sentence(s): " + " | ".join(dropped)[:300])
    put("response_incomplete", not incomplete, incomplete)
    put("response_unsupported", not unsupported, unsupported)

    # end-to-end verdict and failure bucket
    response_ok = st["response_incomplete"]["ok"] and st["response_unsupported"]["ok"]
    gating = [s for s in ("error", "dispute", "safety") if s in st]
    e2e = response_ok and all(st[s]["ok"] for s in gating)
    failure = None
    failed = [s for s in STAGE_ORDER if s in st and st[s]["ok"] is False]
    if not e2e:
        first = failed[0] if failed else "response_incomplete"
        failure = {"bucket": STAGE_BUCKET[first], "stage": first, "detail": st.get(first, {}).get("detail")}
        if failure["bucket"] == "tool_call_error":
            failure["cause"] = error_cause(str(failure["detail"]))
    metrics = state.get("agent_metrics") or {}
    return {
        "turn": rec["turn"], "user": rec["user"], "reply": reply, "stages": st, "e2e_correct": e2e,
        "failure": failure, "recovered": [s for s in failed if e2e], "judge": verdict,
        "wall_seconds": rec.get("wall_seconds"), "langsmith_run_id": rec.get("langsmith_run_id"),
        "llm_calls": len(metrics), "tool_calls": tool_calls_of(state),
        "tokens": sum((m or {}).get("total_tokens") or 0 for m in metrics.values()),
        "cost_usd": sum((m or {}).get("model_cost_with_cache") or 0 for m in metrics.values()),
    }


# ----------------------------------------------------------------------------- one case
def task_success(case: dict, run: dict, scored: list[dict], live_txn_count: int) -> tuple[bool, str]:
    task = case["task"]
    ttype = task if isinstance(task, str) else task["type"]
    spec = {} if isinstance(task, str) else task
    db = run.get("db") or {}
    statuses = run.get("dispute_statuses") or []
    last_reply = (run["turns"][-1].get("reply") or "") if run["turns"] else ""
    if spec.get("db_unchanged") and db.get("transactions_count") != live_txn_count:
        return False, "transactions table changed"
    if ttype in ("answer", "refused", "refused_or_safe_answer"):
        ok = bool(scored) and all(s["e2e_correct"] for s in scored)
        return ok, "" if ok else "a scored turn was wrong"
    new = db.get("new_disputes") or []
    targets = as_list(spec.get("transaction_id"))
    if ttype == "dispute_case_raised":
        hit = [d for d in new if (not targets or d["transaction_id"] in targets)
               and (not spec.get("reason") or d["reason"] == spec["reason"])]
        if hit and len(new) == 1:
            return True, f"case {hit[0]['case_id']} ({hit[0]['reason']})"
        return False, f"cases raised: {[(d['transaction_id'], d['reason']) for d in new]}; statuses {'>'.join(statuses)}; stop={run.get('stop_reason')}"
    if ttype == "fraud_handoff":
        ok = "suspected_fraud" in statuses and not new
        return ok, "" if ok else f"statuses {'>'.join(statuses)}; cases {len(new)}"
    if ttype == "ineligible_explained":
        ok = "not_eligible" in statuses and not new
        if ok and spec.get("must_mention"):
            ok = all(contains_any(last_reply, m) for m in spec["must_mention"])
        return ok, "" if ok else f"statuses {'>'.join(statuses)}; cases {len(new)}"
    return False, f"unknown task {ttype}"


def dialogue_failure(scored: list[dict], run: dict, why: str) -> dict:
    """Bucket for a dispute dialogue that missed its outcome: the first failed stage on a
    turn with gold (even if that turn's reply looked fine, e.g. a complaint routed to SQL),
    else the first app error on any turn (e.g. truncated output while the customer kept
    answering), else a wrong dispute decision."""
    for s in scored:
        failed = [st for st in STAGE_ORDER if st in s["stages"] and s["stages"][st]["ok"] is False]
        if failed:
            f = {"bucket": STAGE_BUCKET[failed[0]], "stage": failed[0], "detail": s["stages"][failed[0]]["detail"]}
            if f["bucket"] == "tool_call_error":
                f["cause"] = error_cause(str(f["detail"]))
            return f
    for t in run["turns"]:
        fb = (t.get("state") or {}).get("fallback_response") or {}
        err = t.get("error") or (t.get("logged_errors") or [None])[0]
        if err or fb.get("fallback_reason") == "exception":
            detail = f"turn {t['turn']}: {err or 'exception in ' + str(fb.get('fallback_node'))}"
            return {"bucket": "tool_call_error", "stage": "error", "detail": detail, "cause": error_cause(detail)}
    return {"bucket": "reasoning_error", "stage": "dispute", "detail": why}


async def score_run(run_dir: Path, use_judge: bool) -> dict:
    gold = {c["id"]: c for c in load_cases()["cases"]}
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    runs = [json.loads(l) for l in (run_dir / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l]
    run_date = date.fromisoformat(meta.get("run_date", date.today().isoformat()))
    warnings = []
    if run_date != date.today():
        warnings.append(f"run date {run_date} != today; relative-date reference SQL is recomputed for today")
    if meta.get("cases_sha256") != hashlib.sha256(CASES_FILE.read_bytes()).hexdigest():
        warnings.append("golden_cases.yaml changed since this run")
    live_txn = run_sql("SELECT COUNT(*) FROM transactions")[1][0][0]
    judge = Judge(run_dir, use_judge)
    ref_cache: dict = {}
    out = []
    for run in runs:
        case = gold.get(run["id"])
        if case is None:
            warnings.append(f"{run['id']} not in the golden set; skipped")
            continue
        scored = []
        for rec in run["turns"]:
            ref = rec.get("expect_ref")
            if not ref:
                continue
            if ref == "expect_first":
                exp = case["expect_first"]
            elif ref.startswith("interjections"):
                exp = case["interjections"][int(ref.split("[")[1].rstrip("]"))]["expect"]
            else:
                exp = case["turns"][int(ref.split("[")[1].rstrip("]"))]["expect"]
            scored.append(await score_turn(case, rec, exp, judge, run_date, ref_cache))
        ok, why = task_success(case, run, scored, live_txn)
        # dialogue cases: a wrong outcome with every scored turn right is still a failure;
        # put it in the bucket of the dispute decision it ended on
        case_failure = None
        if not ok:
            case_failure = dialogue_failure(scored, run, why) if is_dialogue(case) else \
                next((s["failure"] for s in scored if not s["e2e_correct"]), None) or \
                {"bucket": "reasoning_error", "stage": "response_incomplete", "detail": why}
        turns = run["turns"]
        out.append({
            "id": run["id"], "category": case["category"], "split": case["split"], "repeat": run.get("repeat", 0),
            "dialogue": is_dialogue(case), "task_success": ok, "task_detail": why, "case_failure": case_failure,
            "scored_turns": scored, "n_turns": len(turns), "stop_reason": run.get("stop_reason"),
            "dispute_statuses": run.get("dispute_statuses"),
            "wall_seconds": sum(t.get("wall_seconds") or 0 for t in turns),
            "llm_calls": sum(len((t.get("state") or {}).get("agent_metrics") or {}) for t in turns),
            "tool_calls": sum(tool_calls_of(t.get("state") or {}) for t in turns),
            "tokens": sum(sum((m or {}).get("total_tokens") or 0 for m in ((t.get("state") or {}).get("agent_metrics") or {}).values()) for t in turns),
            "cost_usd": sum(sum((m or {}).get("model_cost_with_cache") or 0 for m in ((t.get("state") or {}).get("agent_metrics") or {}).values()) for t in turns),
            "turn_walls": [t.get("wall_seconds") or 0 for t in turns],
        })
    judge.save()
    if judge.errors:
        warnings.append(f"JUDGE FAILED on {len(judge.errors)} reply(ies); those turns were scored by the "
                        f"deterministic checks only. First error: {judge.errors[0]}")
    return {"meta": meta, "cases": out, "warnings": warnings, "judge_calls": judge.calls,
            "judge_errors": len(judge.errors),
            "judge_enabled": use_judge}


# ----------------------------------------------------------------------------- summary + report
def summarise(res: dict) -> dict:
    cases = res["cases"]
    turns = [t for c in cases for t in c["scored_turns"]]
    stage_tot, stage_ok = Counter(), Counter()
    for t in turns:
        for name, s in t["stages"].items():
            if s["ok"] is not None:
                stage_tot[name] += 1
                stage_ok[name] += bool(s["ok"])
    buckets = Counter()
    for c in cases:
        if c["dialogue"]:
            if c["case_failure"]:
                buckets[c["case_failure"]["bucket"]] += 1
        else:
            for t in c["scored_turns"]:
                if t["failure"]:
                    buckets[t["failure"]["bucket"]] += 1
    causes = Counter()
    for c in cases:
        fails = [c["case_failure"]] if c["dialogue"] else [t["failure"] for t in c["scored_turns"]]
        for f in fails:
            if f and f.get("bucket") == "tool_call_error":
                causes[f.get("cause", "other")] += 1
    walls = [w for c in cases for w in c["turn_walls"]]
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else 0
    by_cat = defaultdict(lambda: [0, 0])
    for c in cases:
        by_cat[c["category"]][0] += c["task_success"]
        by_cat[c["category"]][1] += 1
    sel_k = sum(stage_ok[s] for s in ("routes", "subagents", "dispute_gate"))
    sel_n = sum(stage_tot[s] for s in ("routes", "subagents", "dispute_gate"))
    single = [t for c in cases if not c["dialogue"] for t in c["scored_turns"]]
    per_repeat = defaultdict(lambda: [0, 0])
    for c in cases:
        per_repeat[c["repeat"]][0] += c["task_success"]
        per_repeat[c["repeat"]][1] += 1
    rates = [k / n for k, n in per_repeat.values() if n]
    return {
        "n_cases": len(cases), "n_scored_turns": len(turns),
        "e2e_accuracy": {"k": sum(t["e2e_correct"] for t in single), "n": len(single)},
        "task_completion": {"k": sum(c["task_success"] for c in cases), "n": len(cases)},
        "subagent_selection": {"k": sel_k, "n": sel_n},
        "stages": {s: {"k": stage_ok[s], "n": stage_tot[s]} for s in STAGE_ORDER if stage_tot[s]},
        "failure_buckets": {b: buckets[b] for b in BUCKETS},
        "tool_error_causes": dict(causes),
        "recovered": dict(Counter(s for t in turns for s in t["recovered"])),
        "calls_per_task": {"llm_calls": round(statistics.mean([c["llm_calls"] for c in cases]), 2) if cases else 0,
                           "tool_calls": round(statistics.mean([c["tool_calls"] for c in cases]), 2) if cases else 0,
                           "turns": round(statistics.mean([c["n_turns"] for c in cases]), 2) if cases else 0},
        "latency_per_turn_s": {"mean": round(statistics.mean(walls), 2) if walls else 0,
                               "p50": round(q(walls, 0.5), 2), "p95": round(q(walls, 0.95), 2)},
        "cost_per_task_usd": round(statistics.mean([c["cost_usd"] for c in cases]), 5) if cases else 0,
        "tokens_per_task": round(statistics.mean([c["tokens"] for c in cases])) if cases else 0,
        "total_cost_usd": round(sum(c["cost_usd"] for c in cases), 4),
        "by_category": {k: {"k": v[0], "n": v[1]} for k, v in sorted(by_cat.items())},
        "by_split": {s: {"k": sum(c["task_success"] for c in cases if c["split"] == s),
                         "n": sum(1 for c in cases if c["split"] == s)} for s in ("dev", "test")},
        "repeats": {"n": len(per_repeat), "task_completion_mean": round(statistics.mean(rates), 3) if rates else None,
                    "task_completion_sd": round(statistics.pstdev(rates), 3) if len(rates) > 1 else None},
    }


def write_report(run_dir: Path, res: dict, summ: dict) -> None:
    m = res["meta"]
    L = [f"# End-to-end evaluation: {m.get('label')}", "",
         f"Run `{m.get('run_dir')}` on {m.get('run_date')} | commit `{m.get('git_commit')}` | "
         f"provider **{m.get('provider')}** ({m.get('model')}) | embeddings {m.get('embedding')} | "
         f"retrieval {m.get('retrieval_mode')} top-{m.get('top_k')} | citations {m.get('citation_policy')}",
         "Judge: " + (f"Claude {JUDGE_MODEL} ({res['judge_calls']} new calls, rest cached"
                      + (f"; **{res['judge_errors']} FAILED**" if res.get("judge_errors") else "") + ")"
                      if res["judge_enabled"] else "off (deterministic checks only)")
         + f" | LangSmith project: {m.get('langsmith_project') or 'tracing off'}", ""]
    for w in res["warnings"]:
        L.append(f"> warning: {w}")
    s = summ
    L += ["", "## Headline", "", "| Metric | Value |", "|---|---|",
          f"| End-to-end accuracy (single-turn & follow-up replies) | {pct(**s['e2e_accuracy'])} |",
          f"| Task completion rate (all cases, disputes checked in the DB) | {pct(**s['task_completion'])} |",
          f"| Sub-agent selection accuracy (gate + supervisor routes + sub-agents run) | {pct(**s['subagent_selection'])} |",
          f"| LLM calls per task / tool calls per task / turns per task | {s['calls_per_task']['llm_calls']} / {s['calls_per_task']['tool_calls']} / {s['calls_per_task']['turns']} |",
          f"| Latency per turn (wall clock) mean / p50 / p95 | {s['latency_per_turn_s']['mean']}s / {s['latency_per_turn_s']['p50']}s / {s['latency_per_turn_s']['p95']}s |",
          f"| Cost per task / tokens per task / total cost | ${s['cost_per_task_usd']} / {s['tokens_per_task']} / ${s['total_cost_usd']} |"]
    if s["repeats"]["n"] > 1:
        L.append(f"| Task completion over {s['repeats']['n']} repeats | {s['repeats']['task_completion_mean']} ± {s['repeats']['task_completion_sd']} (sd) |")
    L += ["", "## Failure taxonomy (first failing stage)", "", "| Bucket | Count |", "|---|---|"]
    L += [f"| {b} | {n} |" for b, n in s["failure_buckets"].items()]
    if s.get("tool_error_causes"):
        L += ["", "Tool-call errors by cause: " + ", ".join(f"{k} {v}" for k, v in s["tool_error_causes"].items())
              + " (api_rate_limit = the shared capstone key's concurrency cap, i.e. load, not app quality)"]
    if s["recovered"]:
        L += ["", "Stages that failed but the reply was still right (recovered): " +
              ", ".join(f"{k} {v}" for k, v in s["recovered"].items())]
    L += ["", "## Stage accuracy", "", "| Stage | Accuracy |", "|---|---|"]
    L += [f"| {k} | {pct(**v)} |" for k, v in s["stages"].items()]
    L += ["", "## Task completion by category", "", "| Category | Completed |", "|---|---|"]
    L += [f"| {k} | {pct(**v)} |" for k, v in s["by_category"].items()]
    L += ["", f"Split: dev {pct(**s['by_split']['dev'])}, test {pct(**s['by_split']['test'])}", "",
          "## Failures", ""]
    for c in res["cases"]:
        if c["task_success"]:
            continue
        L.append(f"### {c['id']} ({c['category']}, {c['split']}){' r' + str(c['repeat']) if c['repeat'] else ''}")
        if c["dialogue"]:
            L.append(f"- outcome: {c['task_detail']}")
        for t in c["scored_turns"]:
            if t["e2e_correct"]:
                continue
            f = t["failure"] or {}
            L.append(f"- turn {t['turn']}: **{f.get('bucket')}** at `{f.get('stage')}` - "
                     f"{json.dumps(f.get('detail'), default=str)[:400]}")
            L.append(f"  - user: {t['user'][:200]}")
            L.append(f"  - reply: {t['reply'][:300]!r}")
            if t.get("langsmith_run_id"):
                L.append(f"  - LangSmith run: `{t['langsmith_run_id']}`")
        L.append("")
    (run_dir / "report.md").write_text("\n".join(L), encoding="utf-8")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--judge", action="store_true", help=f"grade replies with Claude ({JUDGE_MODEL}); cached")
    args = ap.parse_args()
    res = await score_run(args.run_dir, args.judge)
    summ = summarise(res)
    with open(args.run_dir / "scored.jsonl", "w", encoding="utf-8") as fh:
        for c in res["cases"]:
            fh.write(json.dumps(c, default=str) + "\n")
    (args.run_dir / "summary.json").write_text(json.dumps({"meta": res["meta"], "summary": summ,
                                                           "warnings": res["warnings"]}, indent=2), encoding="utf-8")
    write_report(args.run_dir, res, summ)
    print(json.dumps(summ, indent=1)[:2500])
    print(f"\nwrote {args.run_dir / 'report.md'}")
    if res.get("judge_errors"):
        print(f"WARNING: the judge failed on {res['judge_errors']} reply(ies); see the report's warnings.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
