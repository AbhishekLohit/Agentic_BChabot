"""Step 9: measure each small-model fix, one at a time, on the frozen eval set.

Each rung adds ONE change to the rung before it, so the difference between two
rows is the effect of that change alone:

    0 baseline        dense top-8, plain-text generation, no citation check
    1 +structured     JSON (structured) output, as the app uses
    2 +short context  top-4 instead of top-8 (fits Qwen3-8B's 8K window comfortably)
    3 +hybrid         dense + BM25 with reciprocal rank fusion
    4 +reranker       cross-encoder re-scores the hybrid top-30, keeps the best 4
    5 +strict cites   invalid citations removed, uncited sentences dropped, refuse when unsupported

    python eval/rag/ablation.py --split dev            # all rungs (≈ 6 x questions x 2 LLM calls)
    python eval/rag/ablation.py --rungs 0 5 --limit 5  # quick check
    python eval/rag/ablation.py --report results/<time>_ablation   # rebuild the table only

Writes results/<time>_ablation/{rung_*.jsonl, summary.json, report.md, small_model_section.md}.
Re-running with --resume <dir> skips questions already answered in each rung.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import evaluate as ev  # noqa: E402

RUNGS = [
    ("baseline", "Baseline (dense top-8, plain text, no citation check)",
     {"mode": "dense", "top_k": 8, "generation": "plain", "citation_policy": "off"}),
    ("structured", "+ structured output",
     {"mode": "dense", "top_k": 8, "generation": "structured", "citation_policy": "off"}),
    ("short_context", "+ shorter context (top-4)",
     {"mode": "dense", "top_k": 4, "generation": "structured", "citation_policy": "off"}),
    ("hybrid", "+ hybrid search (BM25 + dense, RRF)",
     {"mode": "hybrid", "top_k": 4, "generation": "structured", "citation_policy": "off"}),
    ("reranker", "+ cross-encoder reranker",
     {"mode": "hybrid_rerank", "top_k": 4, "generation": "structured", "citation_policy": "off"}),
    ("strict_citations", "+ strict citations / abstaining",
     {"mode": "hybrid_rerank", "top_k": 4, "generation": "structured", "citation_policy": "strict"}),
]

METRICS = [  # (label, key path, higher is better)
    ("Context recall", ("context_recall",), True),
    ("Key-fact coverage", ("correctness", "key_fact_coverage"), True),
    ("All facts correct", ("correctness", "all_facts_correct"), True),
    ("Faithfulness", ("faithfulness",), True),
    ("Citation precision", ("citation_precision",), True),
    ("Abstains on unanswerable", ("abstention_accuracy_unanswerable",), True),
    ("False abstention", ("false_abstention_rate",), False),
    ("Context chars", ("avg_context_chars",), False),
    ("Seconds / answer", ("avg_generation_seconds",), False),
]


def get(d, path):
    for p in path:
        d = (d or {}).get(p)
    return d


def report(run_dir: Path, summaries: dict, meta: dict) -> str:
    done = [(n, title) for n, title, _ in RUNGS if n in summaries]
    lines = [f"# Small-model ablation - {meta['split']} split, eval set {meta['eval_version']}", "",
             f"Generator: {meta.get('generator')}; judge: {meta.get('judge')}; index: {meta.get('index')}", "",
             "| Metric | " + " | ".join(t for _, t in done) + " |", "|---" * (len(done) + 1) + "|"]
    for label, path, _ in METRICS:
        cells = [get(summaries[n], path) for n, _ in done]
        if label in ("Context chars", "Seconds / answer"):
            cells = ["-" if c is None else (f"{c:.0f}" if label == "Context chars" else f"{c:.1f}") for c in cells]
        else:
            cells = [ev.fmt(c) for c in cells]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("| Errors | " + " | ".join(str(summaries[n]["n_errors"]) for n, _ in done) + " |")
    lines += ["", "## Effect of each change (vs the rung before it)", ""]
    for (prev, _), (cur, title) in zip(done, done[1:]):
        deltas = []
        for label, path, higher in METRICS[:7]:
            a, b = get(summaries[prev], path), get(summaries[cur], path)
            if a is None or b is None or abs(b - a) < 1e-9:
                continue
            better = (b > a) == higher
            deltas.append(f"{label} {a:.2f} -> {b:.2f} ({'better' if better else 'worse'})")
        lines.append(f"- **{title}**: " + ("; ".join(deltas) if deltas else "no measurable change"))
    text = "\n".join(lines) + "\n"
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"meta": meta, "rungs": summaries}, indent=2),
                                          encoding="utf-8")
    (run_dir / "small_model_section.md").write_text(draft_section(summaries, meta, done), encoding="utf-8")
    return text


def draft_section(s: dict, meta: dict, done: list) -> str:
    """A first draft of the write-up, filled from the measurements. Edit the wording; keep the numbers."""
    def v(n, path):
        x = get(s.get(n), path) if n in s else None
        if x is None:
            return "n/a"
        return f"{x:.0f}" if path == ("avg_context_chars",) else ev.fmt(x)
    out = ["## Compensating for a small model (Qwen3-8B)", "",
           f"All numbers: {meta['split']} split of the frozen eval set ({meta['eval_version']}), "
           f"generator {meta.get('generator')}, judged by {meta.get('judge')}. "
           "Each fix was added on top of the previous one, so each row isolates one change.", "",
           "| Fix | What changed | Before | After |", "|---|---|---|---|"]
    rows = [("structured", "baseline", "Structured output", "JSON schema instead of free text",
             ("faithfulness",), "faithfulness"),
            ("short_context", "structured", "Shorter context for the 8K window", "top-8 -> top-4 chunks",
             ("correctness", "key_fact_coverage"), "key-fact coverage"),
            ("hybrid", "short_context", "Hybrid search", "BM25 + dense, reciprocal rank fusion",
             ("context_recall",), "context recall"),
            ("reranker", "hybrid", "Reranker", "cross-encoder over the top-30 hybrid hits",
             ("context_recall",), "context recall"),
            ("strict_citations", "reranker", "Strict citations / abstaining",
             "invalid cites removed, uncited sentences dropped, refuse without support",
             ("citation_precision",), "citation precision")]
    for cur, prev, fix, what, path, name in rows:
        out.append(f"| {fix} | {what} | {name} {v(prev, path)} | {name} {v(cur, path)} |")
    out += ["", "Notes to write from the table above:",
            f"- Context size: {v('baseline', ('avg_context_chars',))} -> "
            f"{v('short_context', ('avg_context_chars',))} characters per prompt.",
            f"- Abstention on unanswerable questions: {v('baseline', ('abstention_accuracy_unanswerable',))} -> "
            f"{v('strict_citations', ('abstention_accuracy_unanswerable',))}; false abstention "
            f"{v('baseline', ('false_abstention_rate',))} -> {v('strict_citations', ('false_abstention_rate',))} "
            "(the cost of being strict).",
            "- Diagnosis: compare context recall with key-fact coverage. High recall but low coverage means "
            "the 8B generator is the bottleneck; low recall means retrieval is.", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rungs", nargs="+", type=int, help="rung numbers to run (default: all)")
    ap.add_argument("--chunk-strategy", choices=["fixed", "structure", "structure_header"])
    ap.add_argument("--judge-provider")
    ap.add_argument("--resume", help="continue an earlier ablation folder")
    ap.add_argument("--report", help="only rebuild report.md from an ablation folder (no LLM calls)")
    ap.add_argument("--allow-unfrozen", action="store_true")
    args = ap.parse_args()

    if args.report:
        run_dir = Path(args.report)
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        summaries = {n: ev.summarize_answers([json.loads(l) for l in (run_dir / f"rung_{i}_{n}.jsonl").open(encoding="utf-8")])
                     for i, (n, _, _) in enumerate(RUNGS) if (run_dir / f"rung_{i}_{n}.jsonl").is_file()}
        print(report(run_dir, summaries, meta))
        return 0

    version = ev.check_freeze(args.allow_unfrozen)
    if args.resume:
        run_dir = Path(args.resume)
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        args.split, args.limit = meta["split"], meta.get("limit")
        args.chunk_strategy, args.judge_provider = meta.get("chunk_strategy"), meta.get("judge_provider")
    else:
        run_dir = ev.RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_ablation"
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = {"run_at": datetime.now().isoformat(timespec="seconds"), "split": args.split, "limit": args.limit,
                "eval_version": version, "chunk_strategy": args.chunk_strategy,
                "judge_provider": args.judge_provider}
    items = [it for it in ev.load_eval() if args.split == "all" or it["split"] == args.split]
    if args.limit:
        items = items[: args.limit]

    from app_setting import AppSettings
    s = AppSettings()
    vs, meta["index"] = ev.build_index(None, None, args.chunk_strategy)
    generator, judge = ev.load_llms(args.judge_provider)
    meta["generator"] = s.LLM_PROVIDER
    meta["judge"] = f"{args.judge_provider or s.LLM_PROVIDER} (temperature 0)"
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    wanted = set(args.rungs) if args.rungs else set(range(len(RUNGS)))
    summaries = {}
    for i, (name, title, opts) in enumerate(RUNGS):
        out_file = run_dir / f"rung_{i}_{name}.jsonl"
        if i in wanted:
            print(f"\n=== Rung {i}: {title} {opts}")
            done = set()
            if out_file.is_file():
                ok = [r for r in map(json.loads, out_file.open(encoding="utf-8")) if not r.get("error")]
                done = {r["id"] for r in ok}
                out_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ok), encoding="utf-8")
            asyncio.run(ev.run_answers(items, vs, generator, judge, out_file, done, opts))
        if out_file.is_file():
            summaries[name] = ev.summarize_answers([json.loads(l) for l in out_file.open(encoding="utf-8")])
    print("\n" + report(run_dir, summaries, meta))
    print(f"Saved to {run_dir.relative_to(ev.ROOT)} (draft write-up: small_model_section.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
