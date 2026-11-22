"""
Build the help-document knowledge base from the Hugging Face dataset
Vishykm/adaption-banking-and-digital-payments (data/train_0.jsonl).

    python data/prepare_knowledge_base.py                 # build data/help_documents/
    python data/prepare_knowledge_base.py --dry-run       # report only, write nothing

What it does:
  1. takes the knowledge text from the `original_context` field (each passage appears
     twice in the file, with two different user questions) and keeps one copy;
  2. drops passages that name a specific bank or bank-owned app (project rule:
     no specific bank names), and passages too short to be useful;
  3. writes one Markdown file per passage to data/help_documents/
     ("# <topic>" heading, then the passage), and data/knowledge_base_manifest.csv
     recording each document's topic, original source and the dataset's questions.
"""
import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "raw" / "train_0 (1).jsonl"
OUT_DIR = ROOT / "data" / "help_documents"
MANIFEST = ROOT / "data" / "knowledge_base_manifest.csv"
EXCLUDED = ROOT / "data" / "knowledge_base_excluded.csv"
MIN_CHARS = 150

# Specific banks and bank-owned apps/portals. Regulators and public systems (RBI, NPCI,
# UPI, BBPS) and general payment apps (PhonePe, Google Pay, Paytm) are allowed.
BANK_NAMES = [
    r"SBI", r"State Bank", r"YONO", r"OnlineSBI", r"HDFC", r"ICICI", r"iMobile", r"Axis Bank", r"Axis",
    r"Kotak", r"IndusInd", r"IndusNet", r"Union Bank", r"Bank of Baroda", r"BoB World",
    r"(?<!Reserve )Bank of India", r"Punjab National", r"PNB", r"Canara", r"Yes Bank", r"IDFC",
    r"Federal Bank", r"Indian Bank", r"Indian Overseas Bank", r"Central Bank of India", r"UCO Bank",
    r"IDBI", r"Bandhan", r"RBL", r"AU Small Finance", r"Equitas", r"Ujjivan", r"Jana Small Finance",
    r"DBS", r"HSBC", r"Citibank", r"Citi Bank", r"Standard Chartered", r"Airtel Payments Bank",
    r"India Post Payments Bank", r"IPPB", r"Paytm Payments Bank", r"Fino", r"Jio Payments Bank",
    r"NSDL Payments Bank", r"South Indian Bank", r"Karnataka Bank", r"Karur Vysya", r"City Union Bank",
    r"Dhanlaxmi", r"Bank of Maharashtra", r"Punjab & Sind", r"Punjab and Sind",
]
BANK_RE = re.compile(r"\b(" + "|".join(BANK_NAMES) + r")\b")
PREFIX_RE = re.compile(r"^\s*Banking and Digital Payments\s*\n+")


def clean_passage(context: str, subdomain: str) -> str:
    text = PREFIX_RE.sub("", context.strip())
    if text.startswith(subdomain):  # second line repeats the topic, which becomes the heading
        text = text[len(subdomain):]
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:n].strip("-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(line) for line in open(args.input, encoding="utf-8") if line.strip()]
    passages: dict[str, dict] = {}
    for r in rows:
        key = r["original_context"].strip()
        p = passages.setdefault(key, {"subdomain": r["subdomain"].strip(), "source": r.get("source", ""),
                                      "queries": []})
        if r["user_query"] not in p["queries"]:
            p["queries"].append(r["user_query"])

    kept, excluded, seen_text = [], [], set()
    for context, p in passages.items():
        text = clean_passage(context, p["subdomain"])
        banks = sorted(set(BANK_RE.findall(context)))
        reason = ("names a specific bank: " + ", ".join(banks) if banks else
                  "too short" if len(text) < MIN_CHARS else
                  "duplicate text" if text.lower() in seen_text else None)
        if reason:
            excluded.append({**p, "text": text, "reason": reason})
            continue
        seen_text.add(text.lower())
        kept.append({**p, "text": text})

    print(f"Input rows            : {len(rows)}")
    print(f"Distinct passages     : {len(passages)}")
    print(f"Excluded              : {len(excluded)}  " +
          str(Counter(e["reason"].split(":")[0] for e in excluded).most_common()))
    print(f"Kept (documents)      : {len(kept)}")
    lens = sorted(len(k["text"]) for k in kept)
    print(f"Document length chars : min {lens[0]}, median {lens[len(lens)//2]}, max {lens[-1]}")
    print(f"Topics                : {len({k['subdomain'] for k in kept})} distinct; top: "
          f"{Counter(k['subdomain'] for k in kept).most_common(6)}")
    if args.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    old = [f for f in OUT_DIR.glob("kb_*.md")]
    if old:
        raise SystemExit(f"{OUT_DIR} already has {len(old)} kb_*.md files; move them away before rebuilding.")
    with MANIFEST.open("w", encoding="utf-8", newline="") as mf:
        w = csv.writer(mf)
        w.writerow(["doc_id", "file", "topic", "chars", "dataset_source", "dataset_questions"])
        for i, k in enumerate(sorted(kept, key=lambda x: (x["subdomain"], x["text"])), 1):
            doc_id = f"kb_{i:04d}"
            name = f"{doc_id}_{slug(k['subdomain'])}.md"
            (OUT_DIR / name).write_text(f"# {k['subdomain']}\n\n{k['text']}\n", encoding="utf-8")
            w.writerow([doc_id, name, k["subdomain"], len(k["text"]), k["source"], " || ".join(k["queries"])])
    with EXCLUDED.open("w", encoding="utf-8", newline="") as ef:
        w = csv.writer(ef)
        w.writerow(["reason", "topic", "dataset_source", "text"])
        for e in excluded:
            w.writerow([e["reason"], e["subdomain"], e["source"], e["text"]])
    print(f"\nWrote {len(kept)} documents to {OUT_DIR.relative_to(ROOT)}")
    print(f"Manifest: {MANIFEST.relative_to(ROOT)} | excluded list: {EXCLUDED.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
