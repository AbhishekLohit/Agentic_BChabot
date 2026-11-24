"""Week 1: prompt-engineering baseline. Single agent, single prompt.

One LLM call (per sample) gets the whole job: decide the route of a banking question and, for
data questions, write the SQL. No LangGraph, no separate agents. Strategies compared on the
same cases (cases.jsonl):

    zero_shot           baseline: free-text JSON, no examples
    structured_fewshot  schema-enforced structured output + few-shot examples
    cot                 chain-of-thought: reasoning field first, then the answer
    self_consistency    the CoT prompt sampled N times at a higher temperature, majority vote
    decomposition       sub-questions and their answers first, then the query

Run from the project root (uses LLM_PROVIDER etc. from .env, like the app):
    python evaluation/prompt_baseline/run.py --validate-only            # no model calls
    python evaluation/prompt_baseline/run.py --limit 5                  # quick check, all strategies
    python evaluation/prompt_baseline/run.py                            # full comparison
    python evaluation/prompt_baseline/run.py --strategies cot decomposition --ids B21 B24
    python evaluation/prompt_baseline/run.py --report evaluation/prompt_baseline/results/<run>   # rebuild report

Writes results/<time>_<label>/: <strategy>.jsonl (every output, raw text included),
summary.json, report.md (comparison) and failure_modes.md (draft write-up of how the model breaks).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

import scoring  # noqa: E402

STRATEGIES = ("zero_shot", "structured_fewshot", "cot", "self_consistency", "decomposition")
PROMPT_FILE = {"zero_shot": "zero_shot.yml", "structured_fewshot": "structured_fewshot.yml",
               "cot": "cot.yml", "self_consistency": "cot.yml", "decomposition": "decomposition.yml"}
Route = Literal["BankSQL", "HelpCenter", "Dispute", "Out_of_scope"]


class Answer(BaseModel):
    route: Route
    sql: Optional[str] = Field(default=None, description="One read-only SQLite SELECT, or null")


class CoTAnswer(BaseModel):
    reasoning: str = Field(description="Step-by-step reasoning, written before the answer")
    route: Route
    sql: Optional[str] = Field(default=None, description="One read-only SQLite SELECT, or null")


class DecompAnswer(BaseModel):
    sub_questions: list[str] = Field(description="The question broken into small steps, in order")
    sub_answers: list[str] = Field(description="One short answer per sub-question, same order")
    route: Route
    sql: Optional[str] = Field(default=None, description="One read-only SQLite SELECT, or null")


SCHEMAS = {"Answer": Answer, "CoTAnswer": CoTAnswer, "DecompAnswer": DecompAnswer}


# ----------------------------------------------------------------------------- inputs
def load_cases(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_prompt(strategy: str) -> dict:
    return yaml.safe_load((HERE / "prompts" / PROMPT_FILE[strategy]).read_text(encoding="utf-8"))


def render(prompt: dict, question: str, today: str, customer_id: str) -> str:
    task = (HERE / "prompts" / "_task.md").read_text(encoding="utf-8")
    text = prompt["template"].replace("{{TASK}}", task)
    return (text.replace("{{TODAY}}", today).replace("{{CUSTOMER_ID}}", customer_id)
                .replace("{{QUESTION}}", question))


def expected_rows(db_path: str, case: dict):
    if not case.get("reference_sql"):
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [tuple(r) for r in conn.execute(case["reference_sql"]).fetchall()]
    finally:
        conn.close()


# ----------------------------------------------------------------------------- model calls
def _meta(raw: Any, latency: float) -> dict:
    content = getattr(raw, "content", "") if raw is not None else ""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    calls = getattr(raw, "tool_calls", None) or []
    if calls and not content:
        content = json.dumps([c.get("args") for c in calls], ensure_ascii=False, default=str)
    usage = getattr(raw, "usage_metadata", None) or {}
    rmeta = getattr(raw, "response_metadata", None) or {}
    return {"latency": round(latency, 2), "raw_text": content[:4000],
            "tokens": usage.get("total_tokens") or 0, "finish_reason": rmeta.get("finish_reason")}


async def call_once(llm: Any, prompt: dict, text: str) -> tuple[Optional[dict], dict]:
    """One model call. Returns (prediction or None, meta)."""
    messages = [{"role": "user", "content": text}]
    t0 = time.time()
    try:
        if prompt["output"] == "text_json":
            raw = await llm.ainvoke(messages)
            meta = _meta(raw, time.time() - t0)
            return scoring.parse_text_json(meta["raw_text"]), meta
        schema = SCHEMAS[prompt["schema"]]
        out = await llm.with_structured_output(schema, include_raw=True).ainvoke(messages)
        meta = _meta(out.get("raw"), time.time() - t0)
        parsed = out.get("parsed")
        if parsed is None:
            meta["parse_detail"] = str(out.get("parsing_error"))[:300]
            return None, meta
        return parsed.model_dump(), meta
    except Exception as e:  # keep going: an API failure is itself a measured failure mode
        return None, {"latency": round(time.time() - t0, 2), "api_error": f"{type(e).__name__}: {e}"[:300],
                      "raw_text": "", "tokens": 0, "finish_reason": None}


async def run_case(strategy: str, case: dict, llms: dict, args, db_path: str, exp_rows) -> dict:
    prompt = load_prompt(strategy)
    text = render(prompt, case["question"], args.today, args.customer)
    base = {"id": case["id"], "question": case["question"], "expected_route": case["expected_route"],
            "difficulty": case["difficulty"], "strategy": strategy}

    if strategy != "self_consistency":
        pred, meta = await call_once(llms["base"], prompt, text)
        s = scoring.score(case, pred, db_path, args.customer, exp_rows, meta)
        return {**base, **s, "prediction": pred, "meta": meta, "llm_calls": 1}

    # self-consistency: N samples of the CoT prompt, majority vote on (route, SQL result)
    samples = []
    for _ in range(args.samples):
        pred, meta = await call_once(llms["sampling"], prompt, text)
        samples.append((pred, meta, scoring.score(case, pred, db_path, args.customer, exp_rows, meta)))
        if args.delay:
            await asyncio.sleep(args.delay)
    usable = [x for x in samples if x[0] is not None]
    if not usable:
        pred, meta, s = samples[0]
        return {**base, **s, "prediction": pred, "meta": meta, "llm_calls": len(samples), "agreement": 0.0}
    votes = Counter(scoring.result_signature(x[2]) for x in usable)
    winner, count = votes.most_common(1)[0]
    pred, meta, s = next(x for x in usable if scoring.result_signature(x[2]) == winner)
    meta = {**meta, "latency": round(sum(x[1]["latency"] for x in samples), 2),
            "tokens": sum(x[1]["tokens"] for x in samples)}
    return {**base, **s, "prediction": pred, "meta": meta, "llm_calls": len(samples),
            "agreement": round(count / len(usable), 2), "distinct_answers": len(votes),
            "sample_correct": [x[2]["correct"] for x in samples]}


# ----------------------------------------------------------------------------- report
def _pct(x, n):
    return f"{100 * x / n:.0f}%" if n else "-"


def summarize(rows_by_strategy: dict[str, list[dict]]) -> dict:
    summary = {}
    for st, rows in rows_by_strategy.items():
        n = len(rows)
        sql_rows = [r for r in rows if r["expected_route"] == "BankSQL"]
        fails = Counter(f for r in rows for f in r.get("failures", []))
        s = {"n": n,
             "correct": sum(r["correct"] for r in rows),
             "route_ok": sum(r["route_ok"] for r in rows),
             "valid_output": sum(r["valid_output"] for r in rows),
             "n_sql": len(sql_rows),
             "sql_ok": sum(bool(r["sql_ok"]) for r in sql_rows),
             "avg_latency": round(sum(r["meta"]["latency"] for r in rows) / n, 2) if n else None,
             "avg_calls": round(sum(r["llm_calls"] for r in rows) / n, 1) if n else None,
             "avg_tokens": round(sum(r["meta"]["tokens"] for r in rows) / n) if n else None,
             "failures": dict(fails)}
        s["by_difficulty"] = {d: [sum(r["correct"] for r in rows if r["difficulty"] == d),
                                  sum(1 for r in rows if r["difficulty"] == d)] for d in ("easy", "medium", "hard")}
        s["by_route"] = {rt: [sum(r["route_ok"] for r in rows if r["expected_route"] == rt),
                              sum(1 for r in rows if r["expected_route"] == rt)] for rt in scoring.ROUTES}
        if st == "self_consistency":
            ag = [r.get("agreement", 0) for r in rows]
            s["avg_agreement"] = round(sum(ag) / len(ag), 2) if ag else None
            s["questions_with_disagreement"] = sum(1 for r in rows if r.get("distinct_answers", 1) > 1)
        summary[st] = s
    return summary


def write_report(run_dir: Path, meta: dict, rows_by_strategy: dict, summary: dict) -> str:
    sts = list(summary)
    L = [f"# Prompt-engineering baseline: single agent, single prompt",
         "", f"Model: **{meta['model']}** · cases: {meta['n_cases']} · today = {meta['today']} · "
         f"run {meta['run_at']} · self-consistency samples: {meta['samples']}", "",
         "One LLM call per sample routes the question and, for data questions, writes the SQL. "
         "SQL is scored by running it read-only and comparing its RESULT with the reference query's.", "",
         "## Comparison", "",
         "| Strategy | Correct overall | Route accuracy | SQL accuracy (data questions) | Valid output | "
         "Unsafe SQL | No customer filter | Calls / question | Avg latency (s) | Avg tokens |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for st in sts:
        s = summary[st]
        L.append(f"| {st} | {_pct(s['correct'], s['n'])} ({s['correct']}/{s['n']}) | {_pct(s['route_ok'], s['n'])} | "
                 f"{_pct(s['sql_ok'], s['n_sql'])} ({s['sql_ok']}/{s['n_sql']}) | {_pct(s['valid_output'], s['n'])} | "
                 f"{s['failures'].get('unsafe_sql', 0)} | {s['failures'].get('missing_customer_filter', 0)} | "
                 f"{s['avg_calls']} | {s['avg_latency']} | {s['avg_tokens']} |")
    L += ["", "## Correct by difficulty", "", "| Strategy | Easy | Medium | Hard |", "|---|---|---|---|"]
    for st in sts:
        d = summary[st]["by_difficulty"]
        L.append(f"| {st} | " + " | ".join(f"{_pct(*d[k])} ({d[k][0]}/{d[k][1]})" for k in ("easy", "medium", "hard")) + " |")
    L += ["", "## Route accuracy by expected route", "", "| Strategy | " + " | ".join(scoring.ROUTES) + " |",
          "|---" * (len(scoring.ROUTES) + 1) + "|"]
    for st in sts:
        b = summary[st]["by_route"]
        L.append(f"| {st} | " + " | ".join(f"{_pct(*b[r])} ({b[r][0]}/{b[r][1]})" for r in scoring.ROUTES) + " |")
    labels = sorted({f for s in summary.values() for f in s["failures"]},
                    key=lambda f: -sum(s["failures"].get(f, 0) for s in summary.values()))
    L += ["", "## How it breaks: failure modes (count of questions)", ""]
    if labels:
        L += ["| Failure | " + " | ".join(sts) + " |", "|---" * (len(sts) + 1) + "|"]
        for f in labels:
            L.append(f"| {f} | " + " | ".join(str(summary[st]["failures"].get(f, 0)) for st in sts) + " |")
    else:
        L.append("No failures recorded.")
    if "self_consistency" in summary:
        s = summary["self_consistency"]
        L += ["", "## Self-consistency: how stable are the answers?", "",
              f"- Average agreement with the majority answer: **{s.get('avg_agreement')}**",
              f"- Questions where the samples did not all agree: **{s.get('questions_with_disagreement')}** of {s['n']}"]
    L += ["", "## Examples of each failure", ""]
    for f in labels:
        L.append(f"**{f}**: {scoring.FAILURES.get(f, ('',))[0]}")
        shown = 0
        for st in sts:
            for r in rows_by_strategy[st]:
                if f in r.get("failures", []) and shown < 2:
                    shown += 1
                    out = r.get("pred_sql") or r["meta"].get("api_error") or r["meta"].get("parse_detail") \
                        or r["meta"].get("raw_text", "")[:200] or r.get("pred_route")
                    L.append(f"- `{st}` {r['id']} \"{r['question']}\" → route {r.get('pred_route')}; `{str(out)[:220]}`"
                             + (f" (error: {r['sql_error']})" if r.get("sql_error") else ""))
        L.append("")
    text = "\n".join(L) + "\n"
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"meta": meta, "strategies": summary}, indent=2), encoding="utf-8")
    write_failure_modes(run_dir, meta, summary, labels)
    return text


def write_failure_modes(run_dir: Path, meta: dict, summary: dict, labels: list[str]) -> None:
    """Draft of the 'how our small model breaks' section, filled from the measurements."""
    best = max(summary, key=lambda st: summary[st]["correct"]) if summary else None
    L = [f"# How {meta['model']} breaks (single-agent baseline)", "",
         f"Measured on {meta['n_cases']} questions ({meta['run_at']}). Counts are questions, per strategy. "
         "Edit the wording; keep the numbers and examples from report.md.", ""]
    if best:
        L += [f"Best single-prompt strategy: **{best}** ({summary[best]['correct']}/{summary[best]['n']} correct).", ""]
    for f in labels:
        what, guard = scoring.FAILURES.get(f, ("", ""))
        counts = ", ".join(f"{st} {summary[st]['failures'].get(f, 0)}" for st in summary)
        L += [f"## {f}", "", f"- **What happens:** {what}", f"- **How often:** {counts}",
              "- **Example:** see report.md › Examples", f"- **How the multi-agent app handles it:** {guard}", ""]
    (run_dir / "failure_modes.md").write_text("\n".join(L), encoding="utf-8")


# ----------------------------------------------------------------------------- main
def validate(cases: list[dict], db_path: str, args) -> int:
    problems = 0
    for c in cases:
        if c["expected_route"] not in scoring.ROUTES:
            print(f"{c['id']}: bad route {c['expected_route']}"); problems += 1
        if c["expected_route"] == "BankSQL":
            rows = expected_rows(db_path, c)
            if scoring.is_empty(rows):
                print(f"{c['id']}: reference SQL returns nothing"); problems += 1
    for st in STRATEGIES:
        text = render(load_prompt(st), cases[0]["question"], args.today, args.customer)
        if "{{" in text:
            print(f"{st}: unreplaced placeholder"); problems += 1
    counts = Counter(c["expected_route"] for c in cases)
    print(f"{len(cases)} cases {dict(counts)}; strategies {list(STRATEGIES)}; problems: {problems}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    ap.add_argument("--cases", default=str(HERE / "cases.jsonl"))
    ap.add_argument("--db", default=str(ROOT / "transactions.db"))
    ap.add_argument("--ids", nargs="+", help="only these case ids")
    ap.add_argument("--limit", type=int, help="only the first N cases")
    ap.add_argument("--samples", type=int, default=5, help="self-consistency samples (default 5)")
    ap.add_argument("--temperature", type=float, default=0.0, help="temperature for single-call strategies")
    ap.add_argument("--sc-temperature", type=float, default=0.7, help="temperature for self-consistency samples")
    ap.add_argument("--today", default="2026-11-24", help="'today' given to the model (fixed for reproducibility)")
    ap.add_argument("--customer", default="Cust_0410")
    ap.add_argument("--provider", help="LLM provider (default: LLM_PROVIDER from .env)")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between calls (shared API keys)")
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--validate-only", action="store_true", help="check cases and prompts; no model calls")
    ap.add_argument("--report", help="rebuild report.md from an existing run folder; no model calls")
    args = ap.parse_args()

    cases = load_cases(Path(args.cases))
    if args.ids:
        cases = [c for c in cases if c["id"] in set(args.ids)]
    if args.limit:
        cases = cases[: args.limit]
    if args.validate_only:
        return validate(cases, args.db, args)

    if args.report:
        run_dir = Path(args.report)
        meta = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["meta"]
        rows = {p.stem: [json.loads(l) for l in p.open(encoding="utf-8")] for p in sorted(run_dir.glob("*.jsonl"))}
        rows = {st: rows[st] for st in STRATEGIES if st in rows}
        print(write_report(run_dir, meta, rows, summarize(rows)))
        return 0

    from clients.llm_client import get_configured_llm
    from app_setting import AppSettings
    provider = args.provider or AppSettings().LLM_PROVIDER
    llms = {"base": get_configured_llm(provider=provider, temperature=args.temperature),
            "sampling": get_configured_llm(provider=provider, temperature=args.sc_temperature)}
    model = getattr(llms["base"], "model_name", None) or getattr(llms["base"], "model", None) or provider

    run_dir = HERE / "results" / f"{datetime.now():%Y%m%d-%H%M%S}_{args.label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"model": str(model), "provider": provider, "n_cases": len(cases), "today": args.today,
            "run_at": datetime.now().isoformat(timespec="seconds"), "samples": args.samples,
            "temperature": args.temperature, "sc_temperature": args.sc_temperature, "strategies": args.strategies}
    (run_dir / "summary.json").write_text(json.dumps({"meta": meta}, indent=2), encoding="utf-8")

    exp = {c["id"]: expected_rows(args.db, c) for c in cases}
    rows_by_strategy: dict[str, list[dict]] = defaultdict(list)
    for st in args.strategies:
        print(f"\n=== {st}")
        with (run_dir / f"{st}.jsonl").open("w", encoding="utf-8") as f:
            for i, c in enumerate(cases, 1):
                r = asyncio.run(run_case(st, c, llms, args, args.db, exp[c["id"]]))
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n"); f.flush()
                rows_by_strategy[st].append(r)
                mark = "ok " if r["correct"] else "BAD"
                print(f"  [{i}/{len(cases)}] {mark} {c['id']} route={r.get('pred_route')} "
                      f"{','.join(r.get('failures', [])) or ''} ({r['meta']['latency']}s)", flush=True)
                if args.delay:
                    time.sleep(args.delay)
    print("\n" + write_report(run_dir, meta, rows_by_strategy, summarize(rows_by_strategy)))
    print(f"Saved to {run_dir.relative_to(ROOT)} (report.md, failure_modes.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
