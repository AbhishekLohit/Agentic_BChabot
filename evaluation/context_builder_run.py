"""Isolated FinTell Context Builder evaluation.

Offline: python -m evaluation.context_builder_run --validate-only
Live:    python -m evaluation.context_builder_run --live --limit 3

Live mode invokes the project's *actual* context_builder_agent, sequentially,
using its existing prompt and LLM client. It does not call the LangGraph workflow.
The evaluation clock is frozen per invocation without editing production code.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import date, datetime
from difflib import SequenceMatcher
import json
import logging
import os
from pathlib import Path
import re
import sys
from time import perf_counter
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "context_builder_golden_100_audited.jsonl"
DEFAULT_AS_OF = date(2026, 11, 22)
DEFAULT_REPORT = ROOT / "evaluation" / "results" / "context_builder_report.json"
VALID_ROLES = {"user", "assistant"}


def load_dataset(dataset: Path, *, as_of: date) -> list[dict[str, Any]]:
    """Validate every case. Do not silently repair expected outputs."""
    if not dataset.is_file():
        raise ValueError(f"Dataset not found: {dataset}")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(dataset.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON at line {line_number}") from exc
        if not isinstance(case, dict):
            raise ValueError(f"Expected object at line {line_number}")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError(f"Missing or duplicate id at line {line_number}: {case_id!r}")
        seen.add(case_id)
        question = case.get("latest_user_message")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Missing latest_user_message for {case_id}")
        if not isinstance(case.get("category"), str) or not case["category"].strip():
            raise ValueError(f"Missing category for {case_id}")
        expected = case.get("expected_output")
        if not isinstance(expected, dict) or set(expected) != {"contextual_queries"}:
            raise ValueError(f"Invalid expected_output for {case_id}")
        queries = expected["contextual_queries"]
        if (not isinstance(queries, list) or not queries
                or any(not isinstance(q, str) or not q.strip() for q in queries)):
            raise ValueError(f"Invalid contextual_queries for {case_id}")
        history = case.get("chat_history_messages")
        if not isinstance(history, list) or any(
            not isinstance(m, dict) or set(m) != {"role", "content"}
            or m["role"] not in VALID_ROLES
            or not isinstance(m["content"], str)
            for m in history
        ):
            raise ValueError(f"Invalid chat_history_messages for {case_id}")
        if case.get("evaluation_date") != as_of.isoformat():
            raise ValueError(f"Evaluation date mismatch for {case_id}: expected {as_of}")
        if not isinstance(case.get("auto_eval_ready"), bool):
            raise ValueError(f"auto_eval_ready must be a boolean for {case_id}")
        cases.append(case)
    if not cases:
        raise ValueError("Golden dataset is empty")
    return cases


def normalize_query(value: str) -> str:
    """Normalize only casing, whitespace, and terminal punctuation.

    This is not semantic equivalence; paraphrases need human review.
    """
    return re.sub(r"\s+", " ", value).strip().rstrip(".?! ").casefold()


def date_anchors(queries: list[str]) -> list[str]:
    return sorted(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", " ".join(queries)))


def score_output(expected: list[str], actual: Any) -> dict[str, Any]:
    """Exact-match baseline plus review flags. No LLM judge or invented accuracy."""
    if (not isinstance(actual, list) or not actual
            or any(not isinstance(q, str) or not q.strip() for q in actual)):
        return {
            "verdict": "invalid_output", "query_count_match": False,
            "normalized_exact_match": False, "date_anchors_match": None,
            "similarity_diagnostic": None,
        }
    count_match = len(expected) == len(actual)
    exact = count_match and all(
        normalize_query(e) == normalize_query(a)
        for e, a in zip(expected, actual)
    )
    exp_dates, act_dates = date_anchors(expected), date_anchors(actual)
    dates_match = exp_dates == act_dates if exp_dates else None
    if exact:
        verdict = "exact_match"
    elif not count_match or dates_match is False:
        verdict = "potential_mismatch"
    else:
        verdict = "needs_human_review"
    similarity = (
        round(sum(SequenceMatcher(None, normalize_query(e), normalize_query(a)).ratio()
                  for e, a in zip(expected, actual)) / len(expected), 4)
        if count_match else None
    )
    return {
        "verdict": verdict,
        "query_count_match": count_match,
        "normalized_exact_match": exact,
        "date_anchors_match": dates_match,
        "similarity_diagnostic": similarity,
    }


def make_frozen_datetime(reference_date: date) -> type[datetime]:
    """Freeze only the datetime symbol imported in context_builder.py."""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001 - same signature as datetime.now
            return cls(reference_date.year, reference_date.month, reference_date.day, 12, tzinfo=tz)
    return FrozenDatetime


def summarize(results: list[dict[str, Any]], cases: list[dict[str, Any]], *, as_of: date) -> dict[str, Any]:
    counts = Counter(item["verdict"] for item in results)
    timings = [item["latency_seconds"] for item in results if item.get("latency_seconds") is not None]
    usage = [item["total_tokens"] for item in results if item.get("total_tokens") is not None]
    return {
        "evaluation_type": "isolated_context_builder",
        "reference_date": as_of.isoformat(),
        "dataset_total": len(cases),
        "eligible_draft_cases": sum(c.get("auto_eval_ready", False) for c in cases),
        "excluded_pending_rule_decision": [c["id"] for c in cases if not c.get("auto_eval_ready")],
        "executed": len(results),
        "exact_matches_to_draft_ground_truth": counts["exact_match"],
        "requires_human_review": counts["needs_human_review"],
        "potential_mismatches": counts["potential_mismatch"],
        "invalid_outputs_or_errors": counts["invalid_output"] + counts["agent_error"],
        "mean_latency_seconds": round(sum(timings) / len(timings), 3) if timings else None,
        "reported_total_tokens": sum(usage) if usage else None,
        "ground_truth_status": "DRAFT: needs team sign-off",
        "note": (
            "An exact text match is only a reproducible baseline. Semantic equivalents require "
            "human review. These numbers are not validated agent accuracy."
        ),
    }


def build_state(case: dict[str, Any], models_module: Any) -> Any:
    """Construct the real Graph_State with only the Context Builder's inputs."""
    request = models_module.UIUserRequest(
        customer_id="Cust_0410", session_id=f"ctx-eval-{case['id']}",
        user_query=case["latest_user_message"],
    )
    return models_module.Graph_State(
        input_request=request,
        chat_history=[models_module.ChatMessage(**msg) for msg in case["chat_history_messages"]],
        customer_id=request.customer_id,
        context_builder_response=None,
        suprivisor_response=None,
        text2sql_generator_response=None,
        database_query_results=None,
        banksql_queries=None,
        out_of_scope_queries=None,
        fallback_response=None,
        response_generation_response=None,
        agent_metrics=None,
    )


async def evaluate_one(
    case: dict[str, Any], *, model: Any, prompt: Any, logger: logging.Logger,
    context_module: Any, models_module: Any, as_of: date,
) -> dict[str, Any]:
    """Call the REAL Context Builder once and capture only approved diagnostics."""
    state = build_state(case, models_module)
    started = perf_counter()
    expected = case["expected_output"]["contextual_queries"]
    actual: list[str] | None = None
    error_type: str | None = None
    reported_tokens: int | None = None
    try:
        with patch.object(context_module, "datetime", make_frozen_datetime(as_of)):
            updated = await context_module.context_builder_agent(state, model, prompt, logger)
        fallback = getattr(updated, "fallback_response", None)
        if fallback and getattr(fallback, "fallback_flag", False):
            error_type = "AgentFallback" if getattr(fallback, "fallback_reason", None) != "exception" else "AgentExceptionFallback"
        response = getattr(updated, "context_builder_response", None)
        actual = getattr(response, "contextual_queries", None)
        metrics = (getattr(updated, "agent_metrics", None) or {}).get("context_builder")
        if metrics is not None:
            reported_tokens = getattr(metrics, "total_tokens", None)
    except Exception as exc:
        # Do not include exception text (it can contain model data or request details).
        error_type = type(exc).__name__
    score = score_output(expected, actual)
    if error_type:
        score["verdict"] = "agent_error"
    return {
        "id": case["id"], "category": case["category"],
        "latest_user_message": case["latest_user_message"],
        "expected_contextual_queries": expected,
        "actual_contextual_queries": actual,
        **score,
        "latency_seconds": round(perf_counter() - started, 3),
        "total_tokens": reported_tokens,
        "error_type": error_type,
        "ground_truth_review_status": case.get("review_status"),
    }


async def run_live(
    selected: list[dict[str, Any]], *, as_of: date, delay: float,
) -> list[dict[str, Any]]:
    if not os.environ.get("LLM_PROVIDER"):
        raise RuntimeError("Set LLM_PROVIDER explicitly (e.g. 'qwen') before live evaluation")
    sys.path.insert(0, str(ROOT / "src"))
    import app_models as models_module
    import agents.context_builder as context_module
    from app_setting import AppSettings
    from clients.llm_client import get_configured_llm
    from utils.file_utils import read_prompt_file

    settings = AppSettings()
    llm = get_configured_llm()
    prompt = read_prompt_file(settings.context_builder_prompt_path)
    logger = logging.getLogger("fintell.context_builder_evaluation")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False  # suppress exception details and raw requests from production agent
    results: list[dict[str, Any]] = []
    for number, case in enumerate(selected, 1):
        result = await evaluate_one(
            case, model=llm, prompt=prompt, logger=logger,
            context_module=context_module, models_module=models_module, as_of=as_of,
        )
        results.append(result)
        print(f"[{number}/{len(selected)}] {case['id']}: {result['verdict']}", flush=True)
        if number != len(selected) and delay > 0:
            await asyncio.sleep(delay)  # shared group API key allows one active request
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated Context Builder Golden Dataset evaluation")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_AS_OF)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-only", action="store_true", help="Free: validate all 100 cases without an LLM")
    mode.add_argument("--live", action="store_true", help="Send real model requests; may incur charges")
    parser.add_argument("--limit", type=int, default=3, help="Maximum live cases; default 3 for a safe pilot")
    parser.add_argument("--ids", nargs="*", help="Optional case IDs to run")
    parser.add_argument("--include-flagged", action="store_true", help="Also score cases needing rule decisions")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between sequential live requests")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    try:
        cases = load_dataset(args.dataset, as_of=args.as_of)
        eligible = [c for c in cases if c["auto_eval_ready"] or args.include_flagged]
        excluded = [c["id"] for c in cases if not c["auto_eval_ready"]]
        if args.ids:
            unknown = set(args.ids) - {c["id"] for c in cases}
            if unknown:
                parser.error(f"Unknown case IDs: {sorted(unknown)}")
            eligible = [c for c in eligible if c["id"] in args.ids]
        if args.validate_only:
            print(f"Validated {len(cases)} cases; {sum(c['auto_eval_ready'] for c in cases)} eligible drafts; "
                  f"flagged: {', '.join(excluded) or 'none'}. No model calls made.")
            return 0
        if args.limit <= 0 or args.delay < 0:
            parser.error("--limit must be positive and --delay nonnegative")
        selected = eligible[:args.limit]
        if not selected:
            parser.error("No eligible cases selected; flagged cases require --include-flagged")
        print(f"Live evaluation: {len(selected)} cases, fixed date={args.as_of}, "
              "sequential model calls. Ground truth remains a draft.")
        results = asyncio.run(run_live(selected, as_of=args.as_of, delay=args.delay))
        report = {
            "summary": summarize(results, cases, as_of=args.as_of),
            "dataset": str(args.dataset),
            "results": results,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report["summary"], indent=2))
        print(f"Saved report: {args.output}")
        return 0 if results and any(r["verdict"] != "agent_error" for r in results) else 1
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Evaluation setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
