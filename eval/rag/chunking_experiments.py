"""
Chunking experiments: the same retrieval evaluation (recall@1/5/10, MRR) for several
chunking strategies, side by side. No LLM calls; only the local embedding model.

Run from the project root:
    python eval/rag/chunking_experiments.py              # dev split (default)
    python eval/rag/chunking_experiments.py --split test # once, for the final report

Writes eval/rag/results/<timestamp>_chunking/ with report.md, summary.json and one
retrieval_<config>.jsonl per configuration.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as ev  # noqa: E402  (also puts src/ on the path)

# name, strategy, chunk size, overlap (overlap ~15% of the size for fixed windows)
CONFIGS = [
    ("fixed-500", "fixed", 500, 75),
    ("fixed-1000", "fixed", 1000, 150),
    ("fixed-1200 (current)", "fixed", 1200, 200),
    ("fixed-1500", "fixed", 1500, 225),
    ("structure (1 per Q&A)", "structure", 1200, 200),
    ("structure + section header", "structure_header", 1200, 200),
]
GENERATOR_TOP_K = 4  # chunks the help agent actually passes to the LLM


def quotes_split(items, vs) -> float:
    """Share of gold quotes that no single chunk contains (unretrievable by construction)."""
    chunks = [ev.norm(t) for t in vs.get()["documents"]]
    quotes = [ev.norm(e["quote"]) for it in items if it["answerable"]
              for g in it["evidence_groups"] for e in g]
    return sum(not any(q in c for c in chunks) for q in quotes) / len(quotes)


def context_chars(items, vs) -> float:
    """Average characters in the top-4 chunks: how much text the LLM must read."""
    total = [sum(len(d.page_content) for d in vs.similarity_search(it["question"], k=GENERATOR_TOP_K))
             for it in items if it["answerable"]]
    return sum(total) / len(total)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--allow-unfrozen", action="store_true")
    args = ap.parse_args()

    version = ev.check_freeze(args.allow_unfrozen)
    items = [it for it in ev.load_eval() if args.split == "all" or it["split"] == args.split]
    run_dir = ev.RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_chunking_{args.split}"
    run_dir.mkdir(parents=True, exist_ok=True)

    import utils.embeddings.help_document_store as store
    results = []
    for name, strategy, size, overlap in CONFIGS:
        print(f"\n=== {name}")
        store.CHUNK_SIZE, store.CHUNK_OVERLAP = size, overlap  # reset every run
        vs, info = ev.build_index(size, overlap, strategy)
        rows = ev.run_retrieval(items, vs)
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        with (run_dir / f"retrieval_{slug}.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        summary = ev.summarize_retrieval(rows)
        results.append({"name": name, "strategy": strategy, "chunk_size": size if strategy == "fixed" else None,
                         "chunk_overlap": overlap if strategy == "fixed" else None, "index": info,
                         "quotes_split": round(quotes_split(items, vs), 4),
                         "avg_context_chars_top4": round(context_chars(items, vs)),
                         "retrieval": summary, "per_question": {r["id"]: r["first_rank"] for r in rows}})
        o = summary["overall"]
        print(f"    R@1 {o['recall@1']:.3f}  R@5 {o['recall@5']:.3f}  R@10 {o['recall@10']:.3f}  MRR {o['mrr']:.3f}")

    # ---- report
    n = results[0]["retrieval"]["overall"]["n"]
    md = [f"# Chunking experiments ({args.split} split)", "",
          f"- Run: {datetime.now():%Y-%m-%d %H:%M}  |  eval set: {version}  |  {n} answerable questions",
          f"- Embeddings: {results[0]['index']['embedding_provider']}  |  corpus: "
          f"{results[0]['index']['files']} file(s)",
          "", "## Results", "",
          "| Strategy | Chunks | Avg chunk (chars) | R@1 | R@5 | R@10 | MRR | Chars the LLM reads (top 4) | Gold quotes split |",
          "|---|---|---|---|---|---|---|---|---|"]
    best_mrr = max(r["retrieval"]["overall"]["mrr"] for r in results)
    for r in results:
        o = r["retrieval"]["overall"]
        mark = " **(best MRR)**" if o["mrr"] == best_mrr else ""
        md.append(f"| {r['name']}{mark} | {r['index']['chunks']} | {r['index']['avg_chunk_chars']} | "
                  f"{o['recall@1']:.3f} | {o['recall@5']:.3f} | {o['recall@10']:.3f} | {o['mrr']:.3f} | "
                  f"{r['avg_context_chars_top4']} | {r['quotes_split']:.1%} |")
    types = sorted(results[0]["retrieval"]["by_question_type"])
    md += ["", "## Recall@1 by question type", "",
           "| Strategy | " + " | ".join(f"{t} (n={results[0]['retrieval']['by_question_type'][t]['n']})" for t in types) + " |",
           "|---|" + "---|" * len(types)]
    for r in results:
        bt = r["retrieval"]["by_question_type"]
        md.append(f"| {r['name']} | " + " | ".join(f"{bt[t]['recall@1']:.2f}" for t in types) + " |")
    # questions where strategies disagree most (useful for error analysis)
    ids = sorted(results[0]["per_question"])
    def rank(r, i):
        v = r["per_question"].get(i)
        return v if v is not None else 99
    diffs = sorted(ids, key=lambda i: -(max(rank(r, i) for r in results) - min(rank(r, i) for r in results)))[:10]
    md += ["", "## Questions where the strategies disagree most", "",
           "Rank of the first chunk containing gold evidence (1 = top; - = not in top 10).", "",
           "| Question | " + " | ".join(r["name"] for r in results) + " |", "|---|" + "---|" * len(results)]
    for i in diffs:
        md.append(f"| {i} | " + " | ".join(str(r['per_question'][i]) if r['per_question'].get(i) else "-" for r in results) + " |")
    md += ["", "## How to read this", "",
           f"- With {n} questions, one question moves recall by {1/n:.3f}; treat differences smaller than "
           f"about {2/n:.2f} as noise.",
           "- **MRR and R@1 matter most** for the app: the LLM only sees the top 4 chunks, and higher-ranked "
           "evidence is used more reliably by a small model.",
           "- **Chars the LLM reads** is the cost side: smaller chunks put less text in the 8K-token context, "
           "leaving room for more chunks or a longer answer.",
           "- **Gold quotes split** caps recall: a quote cut across two chunks can never be retrieved whole.",
           "- Pick on the dev split, then confirm the winner once on the test split. Re-run after the corpus grows."]
    text = "\n".join(md) + "\n"
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"split": args.split, "eval_version": version,
                                                      "results": results}, indent=2), encoding="utf-8")
    print("\n" + text + f"\nSaved to {run_dir.relative_to(ev.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
