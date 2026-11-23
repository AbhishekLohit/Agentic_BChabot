"""
Validate (and optionally freeze) the RAG evaluation set.

Run from the project root:
    python evaluation/rag/validate_eval_set.py            # check the set and the freeze
    python evaluation/rag/validate_eval_set.py --freeze   # record hashes in MANIFEST.json

Checks, for every answerable question:
  1. each evidence quote exists verbatim in the corpus (case/whitespace-insensitive),
  2. on the stated page, and inside the stated Q&A section,
  3. fully inside at least one chunk produced by the app's CURRENT chunker
     (a quote split across chunks can never be retrieved, so recall would be capped).
Also checks the schema, and that the eval set and corpus still match MANIFEST.json.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EVAL_FILE = ROOT / "evaluation" / "evaluation_cases.jsonl"
MANIFEST = HERE / "MANIFEST.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from evaluation.golden_dataset import load_suite_cases  # noqa: E402

REQUIRED = {"id", "question", "question_type", "answerable", "expected_behavior", "split",
            "gold_doc", "evidence_groups", "reference_answer", "key_facts"}
TYPES = {"paraphrase", "keyword", "factual", "procedural", "reasoning",
         "near_miss", "multi_section", "unanswerable"}


def norm(text: str) -> str:
    """Case-, whitespace- and quote-style-insensitive form used for all matching."""
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_eval():
    return load_suite_cases(EVAL_FILE, "sbi_rag")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true", help="write hashes to MANIFEST.json")
    args = ap.parse_args()

    from app_setting import AppSettings
    from utils.embeddings.help_document_store import (
        CHUNK_OVERLAP, CHUNK_SIZE, _read_document_pages, _split_text, _resolve_project_path)

    settings = AppSettings()
    docs_dir = _resolve_project_path(settings.HELP_DOCUMENTS_DIR)
    rows = load_eval()
    errors, warnings = [], []

    # ---- schema
    ids = Counter(r.get("id") for r in rows)
    for dup, c in ids.items():
        if c > 1:
            errors.append(f"duplicate id {dup}")
    for r in rows:
        missing = REQUIRED - r.keys()
        if missing:
            errors.append(f"{r.get('id')}: missing fields {sorted(missing)}")
            continue
        if r["question_type"] not in TYPES:
            errors.append(f"{r['id']}: unknown question_type {r['question_type']}")
        if r["split"] not in {"dev", "test"}:
            errors.append(f"{r['id']}: split must be dev or test")
        if r["answerable"] and not r["evidence_groups"]:
            errors.append(f"{r['id']}: answerable but has no evidence")
        if not r["answerable"] and r["evidence_groups"]:
            errors.append(f"{r['id']}: unanswerable but has evidence")

    # ---- corpus: pages, sections, chunks (built with the app's own reader and chunker)
    doc_cache = {}

    def doc_info(name):
        if name not in doc_cache:
            path = docs_dir / name
            if not path.is_file():
                doc_cache[name] = None
            else:
                pages = {p: norm(t) for p, t in _read_document_pages(path)}
                full = " ".join(pages[p] for p in sorted(pages, key=lambda x: x or 0))
                marks = [(m.group(1), m.start()) for m in re.finditer(r"\bq(\d+\.\d+) ", full)]
                sections = {}
                for i, (sec, start) in enumerate(marks):
                    end = marks[i + 1][1] if i + 1 < len(marks) else len(full)
                    sections[f"Q{sec}"] = full[start:end]
                chunks = [norm(c) for _, t in _read_document_pages(path) for c in _split_text(t)]
                doc_cache[name] = {"pages": pages, "full": full, "sections": sections, "chunks": chunks}
        return doc_cache[name]

    split_quotes = 0
    for r in rows:
        if not r.get("answerable"):
            continue
        for gi, group in enumerate(r["evidence_groups"], 1):
            for ev in group:
                doc = ev.get("doc") or r["gold_doc"]  # v2: each quote names its own document
                info = doc_info(doc)
                tag = f"{r['id']} group {gi} [{ev.get('section') or doc}]"
                if info is None:
                    errors.append(f"{tag}: document not found in {docs_dir}: {doc}")
                    continue
                q = norm(ev["quote"])
                if q not in info["full"]:
                    errors.append(f"{tag}: quote not found in document: {ev['quote']!r}")
                    continue
                if ev.get("page") is not None and q not in info["pages"].get(ev["page"], ""):
                    errors.append(f"{tag}: quote not on page {ev['page']}")
                section = ev.get("section")
                if section and section in info["sections"] and q not in info["sections"][section]:
                    errors.append(f"{tag}: quote is not inside section {section}")
                elif section and section not in info["sections"]:
                    warnings.append(f"{tag}: section marker {section} not found (skipped section check)")
                if not any(q in c for c in info["chunks"]):
                    split_quotes += 1
                    warnings.append(f"{tag}: quote is split across chunks by the current chunker "
                                    f"(size {CHUNK_SIZE}, overlap {CHUNK_OVERLAP}); it cannot be retrieved as-is")

    # ---- freeze check
    eval_hash = sha256(EVAL_FILE)
    corpus = [{"file": str((docs_dir / n).relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(docs_dir / n)}
              for n in sorted(doc_cache) if doc_cache[n] is not None]
    if MANIFEST.is_file() and not args.freeze:
        m = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if m.get("eval_sha256") != eval_hash:
            errors.append("evaluation_cases.jsonl has CHANGED since it was frozen. Revert it, or bump the "
                          "version and re-freeze (never edit test items after seeing results).")
        if {c["file"]: c["sha256"] for c in m.get("corpus", [])} != {c["file"]: c["sha256"] for c in corpus}:
            warnings.append("corpus files differ from the frozen manifest; re-validate evidence and re-freeze as a new version.")

    # ---- report
    answerable = [r for r in rows if r.get("answerable")]
    print(f"Eval set : {EVAL_FILE.relative_to(ROOT)}  ({len(rows)} questions, "
          f"{len(answerable)} answerable, {len(rows) - len(answerable)} unanswerable)")
    print(f"Splits   : {dict(Counter(r.get('split') for r in rows))}")
    print(f"Types    : {dict(Counter(r.get('question_type') for r in rows))}")
    n_quotes = sum(len(g) for r in answerable for g in r["evidence_groups"])
    print(f"Evidence : {n_quotes} quotes checked; {split_quotes} split by the current chunker "
          f"(size {CHUNK_SIZE}, overlap {CHUNK_OVERLAP})")
    for w in warnings:
        print("WARNING:", w)
    for e in errors:
        print("ERROR  :", e)

    if errors:
        print(f"\nFAILED: {len(errors)} error(s).")
        return 1

    if args.freeze:
        prev = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}
        manifest = {
            "name": "evaluation_cases",
            "suite": "sbi_rag",
            "version": prev.get("version", "v1"),
            "status": "frozen",
            "frozen_on": date.today().isoformat(),
            "eval_file": EVAL_FILE.name,
            "eval_sha256": eval_hash,
            "corpus": corpus,
            "counts": {"total": len(rows), "answerable": len(answerable),
                       "by_split": dict(Counter(r["split"] for r in rows)),
                       "by_type": dict(Counter(r["question_type"] for r in rows))},
            "rules": [
                "Tune on 'dev' only; report final numbers on 'test'.",
                "Do not edit questions or evidence after seeing system outputs; fix mistakes by bumping the version.",
                "Retrieval hit = a retrieved chunk contains the evidence quote (normalised); a question is "
                "fully recalled when every evidence group has at least one hit.",
            ],
        }
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"\nFROZEN: wrote {MANIFEST.relative_to(ROOT)} (eval sha256 {eval_hash[:12]}...)")
    else:
        print("\nOK: all evidence verified" + (" and matches the frozen manifest." if MANIFEST.is_file() else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
