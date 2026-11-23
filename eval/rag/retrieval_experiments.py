"""Step 5/6: compare retrieval modes on the same index - no LLM needed.

    python eval/rag/retrieval_experiments.py                 # dev split, all four modes
    python eval/rag/retrieval_experiments.py --split all --modes dense bm25 hybrid
    python eval/rag/retrieval_experiments.py --chunk-strategy structure_header

Modes (src/utils/help_rag.py):
    dense          embedding similarity only (the original behaviour)
    bm25           keyword search only (rank_bm25)
    hybrid         dense + BM25 fused with reciprocal rank fusion (k=60)
    hybrid_rerank  hybrid top-N re-scored by a cross-encoder (flashrank), best first

Writes results/<time>_retrieval_modes/{summary.json, report.md, <mode>.jsonl}.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from evaluation.rag import evaluate as ev  # noqa: E402  (also puts src/ on sys.path)
from utils.help_rag import RETRIEVAL_MODES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modes", nargs="+", choices=list(RETRIEVAL_MODES), default=list(RETRIEVAL_MODES))
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--chunk-strategy", choices=["fixed", "structure", "structure_header"])
    ap.add_argument("--chunk-size", type=int)
    ap.add_argument("--chunk-overlap", type=int)
    ap.add_argument("--candidates", type=int, help="hybrid/rerank candidate pool (default HELP_RETRIEVAL_CANDIDATES)")
    ap.add_argument("--allow-unfrozen", action="store_true")
    args = ap.parse_args()

    version = ev.check_freeze(args.allow_unfrozen)
    if args.candidates:
        os.environ["HELP_RETRIEVAL_CANDIDATES"] = str(args.candidates)  # read by AppSettings in help_rag
    items = [it for it in ev.load_eval() if args.split == "all" or it["split"] == args.split]
    vs, index_info = ev.build_index(args.chunk_size, args.chunk_overlap, args.chunk_strategy)

    run_dir = ev.RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_retrieval_modes"
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for mode in args.modes:
        t0 = time.time()
        rows = ev.run_retrieval(items, vs, mode=mode)
        secs = time.time() - t0
        with (run_dir / f"{mode}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        summary[mode] = ev.summarize_retrieval(rows)
        summary[mode]["ms_per_query"] = round(1000 * secs / max(len(rows), 1), 1)
        o = summary[mode]["overall"]
        print(f"{mode:14s} R@1 {o['recall@1']:.3f}  R@5 {o['recall@5']:.3f}  R@10 {o['recall@10']:.3f}  "
              f"MRR {o['mrr']:.3f}  ({summary[mode]['ms_per_query']} ms/query)")

    lines = [f"# Retrieval modes - {args.split} split ({len(items)} questions, eval set {version})", "",
             f"Index: {index_info}", "",
             "| Mode | R@1 | R@5 | R@10 | MRR | Group R@5 | ms/query |", "|---|---|---|---|---|---|---|"]
    for mode, s in summary.items():
        o = s["overall"]
        lines.append(f"| {mode} | {o['recall@1']:.3f} | {o['recall@5']:.3f} | {o['recall@10']:.3f} | "
                     f"{o['mrr']:.3f} | {o['group_recall@5']:.3f} | {s['ms_per_query']} |")
    types = sorted({t for s in summary.values() for t in s["by_question_type"]})
    lines += ["", "## Recall@5 by question type", "",
              "| Type | " + " | ".join(summary) + " |", "|---" * (len(summary) + 1) + "|"]
    for t in types:
        cells = [f"{summary[m]['by_question_type'].get(t, {}).get('recall@5', 0):.3f}" for m in summary]
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    lines += ["", "Recall@k = every evidence group of the question is inside the top k chunks. "
              "MRR = 1 / rank of the first chunk holding any evidence."]
    report = "\n".join(lines) + "\n"
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"index": index_info, "split": args.split,
                                                      "eval_version": version, "modes": summary}, indent=2),
                                          encoding="utf-8")
    print("\n" + report + f"Saved to {run_dir.relative_to(ev.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
