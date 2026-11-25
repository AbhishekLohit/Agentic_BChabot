"""Run the golden set through the REAL agent graph and record every stage.

Each case gets a fresh copy of transactions.db (dispute tables and the SQL cache emptied,
optional seeded disputes applied), a new session, no Redis, and the same compiled graph
as /process-debug (src/graph_runner.py). Single-turn and follow-up cases inject their chat
history; dispute cases are dialogues in which a rule-based simulated customer answers
whatever the agent asks (by DisputeResult.status / slot_ids) from the case's `facts`.

Output: evaluation/e2e/results/<timestamp>_<label>/
    cases.jsonl   one line per case run: every turn's full state, trace, metrics, wall time
    meta.json     model, embeddings, retrieval mode, git commit, run date, dataset hash

Usage (repo root, .env configured; capstone chat creds for the reported run):
    python evaluation/e2e/run.py --limit 3 --label smoke
    python evaluation/e2e/run.py --split dev --label dev
    python evaluation/e2e/run.py --ids SQL-01 DSP-01 --label debug
    python evaluation/e2e/run.py --repeat 3 --split dev --label variance
    python evaluation/e2e/run.py --resume evaluation/e2e/results/<dir>      # continue a run
Then: python evaluation/e2e/score.py evaluation/e2e/results/<dir>
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CASES_FILE, DB_PATH, RESULTS_DIR, ROOT, as_list, is_dialogue, load_cases  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
os.chdir(ROOT)

TERMINAL = {"filed", "resolved", "suspected_fraud", "not_eligible", "cancelled", "error"}
MAX_DIALOGUE_TURNS = 14


# ----------------------------------------------------------------------------- serialising
def to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return to_jsonable(obj.model_dump())
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def final_reply(state: dict) -> str:
    rg = state.get("response_generation_response")
    text = getattr(rg, "generated_response", None) if rg is not None else None
    if not text and state.get("fallback_response") is not None:
        text = getattr(state["fallback_response"], "fallback_message", None)
    return text or ""


# ----------------------------------------------------------------------------- simulated customer
def simulate_reply(facts: dict, result: Any, used: dict[str, int]) -> str | None:
    """What the customer says next, given what the dispute agent asked. None = stop."""
    if result is None:
        return None
    status = result.status
    data = result.data or {}
    if status in TERMINAL:
        return None
    scripted = (facts.get("replies") or {}).get(status)
    if scripted:
        i = used.get(status, 0)
        used[status] = i + 1
        if i < len(scripted):
            return scripted[i]
    answers = facts.get("answers") or {}
    if status == "needs_transaction":
        return f"It was {facts['amount']} to {facts['merchant']} on {facts['date']}."
    if status == "needs_payment_confirmation":
        return "Yes, that's the one."
    if status == "needs_transaction_choice":
        return facts.get("choose") or "The first one."
    if status == "needs_reason":
        return facts.get("reason_text") or answers.get("description") or "I want to dispute it."
    if status == "needs_info":
        if data.get("recognition_question"):
            return facts.get("recognises") or "No, I don't recognise it."
        ids = data.get("slot_ids") or []
        parts = [answers.get(i, facts.get("default_answer", "I'm not sure.")) for i in ids]
        return " ".join(parts) or answers.get("description") or "I'm not sure."
    if status == "needs_confirmation":
        return "Yes, that's all correct. Please raise it."
    return None


# ----------------------------------------------------------------------------- per-case database
def prepare_db(case: dict, workdir: Path) -> sqlite3.Connection:
    """A private copy of the demo DB: no old drafts / disputes / cached SQL, optional seeds."""
    path = workdir / "transactions.db"
    shutil.copyfile(DB_PATH, path)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from utils import db_utils
    db_utils.setup_chat_tables(conn)
    for table in ("dispute_drafts", "disputes", "dispute_events", "query_cache",
                  "chat_messages", "chat_sessions", "request_metrics"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    for i, d in enumerate((case.get("setup") or {}).get("existing_disputes", [])):
        db_utils.create_dispute(conn, case.get("customer_id", "Cust_0410"), f"seed-{i}",
                                d["transaction_id"], d["reason"], {"seeded": True})
    return conn


def db_outcome(conn: sqlite3.Connection, seeded: int) -> dict:
    disputes = [dict(r) for r in conn.execute(
        "SELECT case_id, transaction_id, reason, status, session_id FROM disputes")]
    events = [dict(r) for r in conn.execute("SELECT * FROM dispute_events")]
    return {
        "transactions_count": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        "disputes": disputes,
        "new_disputes": [d for d in disputes if not str(d["session_id"]).startswith("seed-")],
        "dispute_events": events,
        "seeded_disputes": seeded,
    }


class _ErrorCapture(logging.Handler):
    """Collects ERROR records logged by the app during one turn (cause of a caught exception)."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.errors: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            msg += f" | {type(exc).__module__}.{type(exc).__name__}: {str(exc)[:200]}"
        self.errors.append(msg[:400])


# ----------------------------------------------------------------------------- one turn
async def one_turn(ctx: dict, graph: Any, case: dict, session_id: str, history: list,
                   user: str, n: int, label: str, run_tag: str) -> dict:
    from app_models import ChatMessage
    from graph_runner import build_graph_state, run_turn
    from langgraph.errors import GraphRecursionError

    state = build_graph_state(user, case.get("customer_id", ctx["customer_id"]), session_id, history)
    config = {"run_name": f"{case['id']}#t{n}",
              "tags": ["fintell-e2e", label, case["category"], case["split"]],
              "metadata": {"case_id": case["id"], "turn": n, "split": case["split"],
                           "category": case["category"], "run": run_tag}}
    rec: dict[str, Any] = {"turn": n, "user": user, "error": None, "langsmith_run_id": None}
    # The app catches its own exceptions (state only says "exception"); keep the logged cause.
    capture = _ErrorCapture()
    app_logger = logging.getLogger("app_logger")
    app_logger.addHandler(capture)
    t0 = time.perf_counter()
    try:
        try:
            from langchain_core.tracers.context import collect_runs
            with collect_runs() as cb:
                response = await run_turn(graph, state, config)
            if cb.traced_runs:
                rec["langsmith_run_id"] = str(cb.traced_runs[0].id)
        except ImportError:
            response = await run_turn(graph, state, config)
    except GraphRecursionError:
        response, rec["error"] = {}, "step_limit"
    except Exception as e:  # recorded as a tool-call error by the scorer
        response, rec["error"] = {}, f"{type(e).__name__}: {e}"
    finally:
        app_logger.removeHandler(capture)
    rec["logged_errors"] = capture.errors
    rec["wall_seconds"] = round(time.perf_counter() - t0, 3)
    rec["reply"] = final_reply(response)
    rec["state"] = to_jsonable({k: v for k, v in response.items() if k not in ("chat_history",)})
    history.append(ChatMessage(role="user", content=user))
    if rec["reply"]:
        history.append(ChatMessage(role="assistant", content=rec["reply"]))
    if ctx["delay"]:
        await asyncio.sleep(ctx["delay"])
    return rec, response


def _stuck(turns: list[dict], n: int = 3) -> bool:
    """The last n turns all failed inside the app with the same logged error."""
    last = turns[-n:]
    if len(last) < n:
        return False
    sigs = [(t.get("logged_errors") or [None])[0] for t in last]
    return sigs[0] is not None and len(set(s.split(" | ")[0] for s in sigs)) == 1


# ----------------------------------------------------------------------------- one case
async def run_case(ctx: dict, case: dict, label: str, repeat: int, run_tag: str) -> dict:
    from app_models import ChatMessage
    from graph_runner import build_agent_graph

    workdir = Path(tempfile.mkdtemp(prefix=f"e2e_{case['id']}_"))
    conn = prepare_db(case, workdir)
    seeded = len((case.get("setup") or {}).get("existing_disputes", []))
    graph = build_agent_graph(ctx["llm"], ctx["prompts"], ctx["logger"], conn, ctx["settings"],
                              dispute_registry=ctx["registry"], help_vector_store=ctx["help_store"])
    session_id = f"eval-{run_tag}-{case['id']}-r{repeat}"
    history = [ChatMessage(role=m["role"], content=m["content"]) for m in case.get("chat_history") or []]
    turns, statuses, stop_reason = [], [], None
    t_case = time.perf_counter()
    try:
        if not is_dialogue(case):
            for n, t in enumerate(case["turns"]):
                rec, _ = await one_turn(ctx, graph, case, session_id, history, t["user"], n, label, run_tag)
                rec["expect_ref"] = f"turns[{n}]"
                turns.append(rec)
        else:
            facts, used = case["facts"], {}
            pending = list(case.get("interjections") or [])
            message, expect_ref, last_result = case["opening"], "expect_first", None
            for n in range(MAX_DIALOGUE_TURNS):
                rec, response = await one_turn(ctx, graph, case, session_id, history, message, n, label, run_tag)
                rec["expect_ref"] = expect_ref
                turns.append(rec)
                result = response.get("dispute_result")
                if result is not None:
                    last_result = result
                    statuses.append(result.status)
                rec["dispute_status"] = getattr(result, "status", None)
                if rec["error"]:
                    stop_reason = "error"
                    break
                if _stuck(turns):
                    stop_reason = "stuck_repeated_errors"
                    break
                inter = next((i for i in pending if last_result is not None
                              and last_result.status in as_list(i["at_status"])), None)
                if inter is not None:
                    pending.remove(inter)
                    message = inter["user"]
                    expect_ref = f"interjections[{case['interjections'].index(inter)}]"
                    continue
                message = simulate_reply(facts, last_result, used)
                expect_ref = None
                if message is None:
                    stop_reason = "terminal" if last_result is not None and last_result.status in TERMINAL \
                        else "no_dispute_result"
                    break
            else:
                stop_reason = "max_turns"
        outcome = db_outcome(conn, seeded)
    finally:
        conn.close()
        shutil.rmtree(workdir, ignore_errors=True)
    return {"id": case["id"], "category": case["category"], "split": case["split"], "repeat": repeat,
            "turns": turns, "dispute_statuses": statuses, "stop_reason": stop_reason,
            "case_wall_seconds": round(time.perf_counter() - t_case, 3), "db": outcome}


# ----------------------------------------------------------------------------- setup
def build_context(delay: float) -> dict:
    from agents.disputes.registry import load_dispute_registry
    from app_setting import AppSettings
    from clients.llm_client import embedding_model_id, get_configured_llm, get_embeddings
    from logging_setup import setup_logging
    from utils.embeddings.help_document_store import create_help_vector_store
    from utils.file_utils import get_all_prompts

    settings = AppSettings()
    if settings.LANGCHAIN_API_KEY:  # same switch as the server's lifespan
        os.environ["LANGCHAIN_TRACING_V2"] = settings.LANGCHAIN_TRACING_V2
        os.environ["LANGCHAIN_API_KEY"] = settings.LANGCHAIN_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = settings.LANGCHAIN_PROJECT
    embeddings = get_embeddings(settings.EMBEDDING_PROVIDER)
    help_store = create_help_vector_store(embeddings, settings)
    return {
        "settings": settings,
        "llm": get_configured_llm(),
        "prompts": get_all_prompts(settings),
        "logger": setup_logging(),
        "registry": load_dispute_registry(settings.dispute_types_path),
        "help_store": help_store,
        "help_chunks": help_store._collection.count(),
        "embedding": embedding_model_id(embeddings),
        "customer_id": "Cust_0410",
        "delay": delay,
    }


def git_commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.call(["git", "diff", "--quiet"], cwd=ROOT) != 0
        return sha + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="run")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--category", nargs="*")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--repeat", type=int, default=1, help="run every case N times (variance)")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between turns (rate limits)")
    ap.add_argument("--resume", type=Path, help="existing results dir: skip cases already run")
    ap.add_argument("--validate-only", action="store_true", help="load everything, run nothing")
    args = ap.parse_args()

    data = load_cases()
    cases = data["cases"]
    if args.ids:
        cases = [c for c in cases if c["id"] in set(args.ids)]
    if args.category:
        cases = [c for c in cases if c["category"] in set(args.category)]
    if args.split != "all":
        cases = [c for c in cases if c["split"] == args.split]
    if args.limit:
        cases = cases[: args.limit]
    for c in cases:
        c.setdefault("customer_id", data.get("customer_id", "Cust_0410"))

    ctx = build_context(args.delay)
    s = ctx["settings"]
    print(f"{len(cases)} case(s) x {args.repeat} | provider={s.LLM_PROVIDER} | help chunks={ctx['help_chunks']} "
          f"| retrieval={s.HELP_RETRIEVAL_MODE} | tracing={'on' if os.environ.get('LANGCHAIN_TRACING_V2') == 'true' else 'off'}")
    if args.validate_only:
        return 0

    out_dir = args.resume or RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cases.jsonl"
    done = set()
    if out_file.exists():
        done = {(json.loads(l)["id"], json.loads(l)["repeat"]) for l in out_file.read_text(encoding="utf-8").splitlines() if l}
    run_tag = out_dir.name
    meta = {
        "label": args.label, "run_dir": out_dir.name, "started": datetime.now().isoformat(timespec="seconds"),
        "run_date": datetime.now().date().isoformat(), "git_commit": git_commit(),
        "cases_sha256": hashlib.sha256(CASES_FILE.read_bytes()).hexdigest(),
        "provider": s.LLM_PROVIDER, "model": getattr(ctx["llm"], "model_name", None) or getattr(ctx["llm"], "model", None),
        "temperature": s.TEMPERATURE, "embedding": ctx["embedding"], "help_chunks": ctx["help_chunks"],
        "retrieval_mode": s.HELP_RETRIEVAL_MODE, "top_k": s.HELP_TOP_K, "citation_policy": s.HELP_CITATION_POLICY,
        "chunk_strategy": s.HELP_CHUNK_STRATEGY, "dispute_window_days": s.DISPUTE_WINDOW_DAYS,
        "langsmith_project": os.environ.get("LANGCHAIN_PROJECT") if os.environ.get("LANGCHAIN_TRACING_V2") == "true" else None,
        "n_cases": len(cases), "repeat": args.repeat, "filters": {"ids": args.ids, "category": args.category,
                                                                  "split": args.split, "limit": args.limit},
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    with open(out_file, "a", encoding="utf-8") as fh:
        for r in range(args.repeat):
            for i, case in enumerate(cases, 1):
                if (case["id"], r) in done:
                    continue
                t0 = time.perf_counter()
                result = await run_case(ctx, case, args.label, r, run_tag)
                fh.write(json.dumps(result, default=str) + "\n")
                fh.flush()
                errs = sum(1 for t in result["turns"] if t["error"])
                print(f"[{r + 1}/{args.repeat}] {i:>3}/{len(cases)} {case['id']:<8} {len(result['turns'])} turn(s) "
                      f"{time.perf_counter() - t0:6.1f}s{'  ERRORS: ' + str(errs) if errs else ''}"
                      f"{'  statuses: ' + '>'.join(result['dispute_statuses']) if result['dispute_statuses'] else ''}")
    meta["finished"] = datetime.now().isoformat(timespec="seconds")
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nwrote {out_file}\nscore it:  python evaluation/e2e/score.py {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
