"""CLI: python -m evaluation.run --dataset evaluation/cases.jsonl --output evaluation/results/run.json"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

from evaluation.scoring import score_case, summarize, VALID_ROUTES

ROOT = Path(__file__).resolve().parent.parent


def load_cases(path: Path) -> list[dict]:
    cases = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError(f"Invalid id on line {number}")
        if item["id"] in seen:
            raise ValueError(f"Duplicate case id: {item['id']}")
        seen.add(item["id"])
        if not isinstance(item.get("question"), str) or not item["question"].strip():
            raise ValueError(f"Invalid question on line {number}")
        if not isinstance(item.get("category"), str):
            raise ValueError(f"Missing category on line {number}")
        if not isinstance(item.get("expected_routes"), list) or not item["expected_routes"]:
            raise ValueError(f"Missing expected_routes on line {number}")
        if any(route not in VALID_ROUTES for route in item["expected_routes"]):
            raise ValueError(f"Invalid expected_routes on line {number}")
        if not isinstance(item.get("customer_id", "Cust_0410"), str):
            raise ValueError(f"Invalid customer_id on line {number}")
        if not isinstance(item.get("history", []), list) or any(
            not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str)
            for m in item.get("history", [])
        ):
            raise ValueError(f"Invalid history on line {number}")
        for key in ("answer_contains", "answer_excludes"):
            if key in item and (not isinstance(item[key], list) or any(not isinstance(v, str) for v in item[key])):
                raise ValueError(f"Invalid {key} on line {number}")
        if "expected_fallback" in item and not isinstance(item["expected_fallback"], bool):
            raise ValueError(f"Invalid expected_fallback on line {number}")
        cases.append(item)
    if not cases:
        raise ValueError("Evaluation dataset is empty")
    return cases


async def run_live(cases: list[dict], db_path: Path, *, help_enabled: bool) -> list[dict]:
    # Import runtime dependencies only for live evaluation; offline unit tests stay lightweight.
    sys.path.insert(0, str(ROOT / "src"))
    from agent_graph import Agent_Graph
    from agent_subraph import Agent_SubGraph
    from app_models import ChatMessage, Graph_State, UIUserRequest
    from app_setting import AppSettings
    from clients.llm_client import get_configured_llm
    from utils.file_utils import get_all_prompts

    settings = AppSettings()
    llm = get_configured_llm()
    prompts = get_all_prompts(settings)
    logger = logging.getLogger("fintell-evaluation")
    logger.addHandler(logging.NullHandler())

    help_store = None
    if help_enabled:
        from utils.embeddings.help_store import HelpStore
        help_store = HelpStore(
            persist_directory=str((ROOT / settings.help_store_dir).resolve()),
            collection_name=settings.help_collection,
            model=settings.EMBED_MODEL,
            top_k=settings.help_top_k,
            threshold=settings.help_similarity_threshold,
        )

    # A PRIVATE SQLite backup prevents the graph's SQL cache and any generated
    # non-SELECT statement from modifying the source transactions database.
    # Turn off carryover history/Redis; each scenario has its own explicit history.
    with tempfile.TemporaryDirectory(prefix="fintell_eval_") as tmp:
        scratch = sqlite3.connect(str(Path(tmp) / "evaluation.db"))
        source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            source.backup(scratch)
        finally:
            source.close()
        reference = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            subgraph = Agent_SubGraph(llm, prompts, logger, db_conn=scratch, redis_client=None)
            graph = Agent_Graph(llm, prompts, logger, subgraph, help_store=help_store)
            results = []
            for item in cases:
                messages = [ChatMessage(**message) for message in item.get("history", [])]
                messages.append(ChatMessage(role="user", content=item["question"]))
                request = UIUserRequest(customer_id=item.get("customer_id", "Cust_0410"), session_id=f"eval-{item['id']}", user_query=item["question"])
                state = Graph_State(
                    input_request=request, chat_history=messages, customer_id=request.customer_id,
                    context_builder_response=None, suprivisor_response=None,
                    text2sql_generator_response=None, database_query_results=None,
                    banksql_queries=None, out_of_scope_queries=None,
                    fallback_response=None, response_generation_response=None, agent_metrics=None,
                )
                start = time.perf_counter()
                try:
                    response = await graph.app.ainvoke(state)
                    result = score_case(item, response, conn=reference)
                    result["latency_seconds"] = round(time.perf_counter() - start, 3)
                except Exception as exc:
                    # Do not leak arbitrary model outputs or customer data into reports.
                    result = {"id": item["id"], "category": item["category"], "checks": {}, "passed": False,
                              "error_type": type(exc).__name__, "latency_seconds": round(time.perf_counter() - start, 3)}
                results.append(result)
                print(f"{item['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
            return results
        finally:
            reference.close()
            scratch.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the FinTell agent graph")
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation" / "cases.jsonl")
    parser.add_argument("--db", type=Path, default=ROOT / "transactions.db")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation" / "results" / "report.json")
    parser.add_argument("--validate-only", action="store_true", help="Validate case structure and reference SQL; do not call models")
    parser.add_argument("--enable-help", action="store_true", help="Load Chroma/embedding model for HelpCenter cases")
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    if args.validate_only:
        conn = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
        from evaluation.scoring import expected_sql_values
        try:
            for case in cases:
                if case.get("expected_sql"):
                    expected_sql_values(conn, case["expected_sql"])
        finally:
            conn.close()
        print(f"Validated {len(cases)} cases and their reference SQL; no LLM calls made.")
        return 0
    results = asyncio.run(run_live(cases, args.db.resolve(), help_enabled=args.enable_help))
    summary = summarize(results)
    payload = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "dataset": str(args.dataset), "summary": summary, "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved: {args.output}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
