"""Validate the golden set against the RAW data, before any model sees it.

Checks:
  schema       ids unique, required fields, known labels, dev/test split
  sql          every reference query runs read-only; results printed for review
               (--snapshot writes them to golden_snapshot.json)
  rag          every evidence doc exists and contains its quote verbatim, and the quote
               is not split across two index chunks (that would cap recall)
  disputes     every target transaction exists, the opening's merchant/amount/date
               finds it with the app's own lookup (checks.find_transactions), its
               eligibility today matches the case, and answer keys are real question ids

Usage (from the repo root):
  python evaluation/e2e/validate.py
  python evaluation/e2e/validate.py --snapshot
  python evaluation/e2e/validate.py --export-review 0.25     # CSV sample for human review
  python evaluation/e2e/validate.py --freeze                 # write MANIFEST.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CASES_FILE, DB_PATH, DOCS_DIR, GUARDRAIL_LABELS, HERE, ROUTES,  # noqa: E402
                    SUBAGENTS, as_list, is_dialogue, load_cases, norm, run_sql)

DISPUTE_STATUSES = {"needs_transaction", "needs_payment_confirmation", "needs_transaction_choice",
                    "not_eligible", "needs_reason", "needs_info", "needs_confirmation", "resolved",
                    "suspected_fraud", "filed", "cancelled", "error"}
TASK_TYPES = {"answer", "refused", "refused_or_safe_answer", "dispute_case_raised", "fraud_handoff",
              "ineligible_explained"}
NOT_ELIGIBLE_CATEGORIES = {"dispute_not_eligible"}


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, case_id: str, msg: str):
        self.errors.append(f"{case_id}: {msg}")

    def warn(self, case_id: str, msg: str):
        self.warnings.append(f"{case_id}: {msg}")


def parse_money(text: str) -> float:
    return float(str(text).replace("£", "").replace(",", "").strip())


def parse_day(text: str) -> date:
    return datetime.strptime(text.strip(), "%d %B %Y").date()


# ----------------------------------------------------------------------------- checks
def check_schema(data: dict, rep: Report) -> list[dict]:
    cases = data.get("cases") or []
    ids = Counter(c.get("id") for c in cases)
    for cid, n in ids.items():
        if not cid or n > 1:
            rep.err(str(cid), f"missing or duplicate id (x{n})")
    for c in cases:
        cid = c.get("id", "?")
        for key in ("category", "split", "task"):
            if key not in c:
                rep.err(cid, f"missing {key}")
        if c.get("split") not in ("dev", "test"):
            rep.err(cid, "split must be dev or test")
        task = c.get("task")
        ttype = task if isinstance(task, str) else (task or {}).get("type")
        if ttype not in TASK_TYPES:
            rep.err(cid, f"unknown task type {ttype!r}")
        if is_dialogue(c):
            for key in ("opening", "facts", "expect_first"):
                if key not in c:
                    rep.err(cid, f"dialogue missing {key}")
            expects = [c.get("expect_first", {})] + [i.get("expect", {}) for i in c.get("interjections", [])]
        else:
            if not c.get("turns"):
                rep.err(cid, "no turns")
            expects = [t.get("expect", {}) for t in c.get("turns", [])]
        for exp in expects:
            for label in as_list(exp.get("guardrail")):
                if label not in GUARDRAIL_LABELS:
                    rep.err(cid, f"unknown guardrail label {label}")
            routes = exp.get("routes") or []
            for r in (x for alt in routes for x in as_list(alt)):
                if r not in ROUTES:
                    rep.err(cid, f"unknown route {r}")
            for s in exp.get("subagents") or []:
                if s not in SUBAGENTS:
                    rep.err(cid, f"unknown subagent {s}")
            for st in as_list((exp.get("dispute") or {}).get("status")):
                if st not in DISPUTE_STATUSES:
                    rep.err(cid, f"unknown dispute status {st}")
    return cases


def check_sql(cases: list[dict], rep: Report) -> dict:
    snapshot = {}
    for c in cases:
        expects = [t.get("expect", {}) for t in c.get("turns", [])] + \
                  [i.get("expect", {}) for i in c.get("interjections", [])]
        for ti, exp in enumerate(expects):
            for si, ref in enumerate(exp.get("sql") or []):
                key = f"{c['id']}#{ti}.{si}"
                try:
                    cols, rows = run_sql(ref["reference"])
                except sqlite3.Error as e:
                    rep.err(c["id"], f"reference SQL fails: {e}")
                    continue
                if "Cust_0410" not in ref["reference"]:
                    rep.err(c["id"], "reference SQL has no customer filter")
                if ref.get("compare") == "scalar" and (len(rows) != 1 or len(rows[0]) != 1):
                    rep.err(c["id"], f"scalar reference returned {len(rows)} rows x {len(rows[0]) if rows else 0} cols")
                empty = not rows or all(v is None for v in rows[0])
                if empty and c.get("category") != "banksql_no_data":
                    rep.warn(c["id"], "reference result is empty")
                snapshot[key] = {"columns": cols, "rows": [list(r) for r in rows]}
    return snapshot


def check_rag(cases: list[dict], rep: Report) -> None:
    try:
        from app_setting import AppSettings
        from utils.embeddings.help_document_store import load_help_documents
        s = AppSettings()
        chunks, _ = load_help_documents(str(DOCS_DIR), s.HELP_CHUNK_STRATEGY)
        by_doc: dict[str, list[str]] = {}
        for d in chunks:
            by_doc.setdefault(Path(d.metadata.get("source", "")).name, []).append(norm(d.page_content))
    except Exception as e:  # chunk check is advisory
        rep.warn("rag", f"could not load index chunks for the split-quote check: {e}")
        by_doc = {}
    for c in cases:
        exps = [t.get("expect", {}) for t in c.get("turns", [])]
        for exp in exps:
            rag = exp.get("rag") or {}
            for ev in rag.get("evidence") or []:
                path = DOCS_DIR / ev["doc"]
                if not path.is_file():
                    rep.err(c["id"], f"evidence doc not found: {ev['doc']}")
                    continue
                if norm(ev["quote"]) not in norm(path.read_text(encoding="utf-8")):
                    rep.err(c["id"], f"quote not in {ev['doc']}: {ev['quote'][:60]}...")
                elif by_doc and not any(norm(ev["quote"]) in ch for ch in by_doc.get(ev["doc"], [])):
                    rep.warn(c["id"], f"quote is split across chunks of {ev['doc']} (caps retrieval recall)")
            if rag.get("answerable") is True and not rag.get("evidence"):
                rep.err(c["id"], "answerable help case without evidence")


def check_disputes(cases: list[dict], rep: Report, today: date) -> None:
    from agents.disputes import checks
    from agents.disputes.registry import load_dispute_registry
    from app_setting import AppSettings
    s = AppSettings()
    registry = load_dispute_registry(s.dispute_types_path)
    slot_ids = {sid.id for reason in registry.types for sid in registry.slots_for(reason)}
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for c in (c for c in cases if is_dialogue(c)):
        f = c["facts"]
        targets = as_list(f["transaction_id"])
        for tid in targets:
            txn = checks.get_transaction(conn, "Cust_0410", tid)
            if txn is None:
                rep.err(c["id"], f"transaction {tid} not found")
                continue
            seeded = {d["transaction_id"] for d in (c.get("setup") or {}).get("existing_disputes", [])}
            elig = checks.check_eligibility(txn, today, s.DISPUTE_WINDOW_DAYS, tid in seeded)
            want_eligible = c["category"] not in NOT_ELIGIBLE_CATEGORIES
            if elig.ok != want_eligible:
                rep.err(c["id"], f"{tid} eligibility today is {elig.code}, case expects "
                                 f"{'eligible' if want_eligible else 'not eligible'}")
        try:
            found = checks.find_transactions(conn, "Cust_0410", parse_money(f["amount"]), parse_day(f["date"]),
                                             f["merchant"], s.DISPUTE_AMOUNT_TOLERANCE,
                                             s.DISPUTE_MERCHANT_MATCH, s.DISPUTE_MAX_CHOICES)
            if not set(targets) & {r["transaction_id"] for r in found}:
                rep.err(c["id"], f"lookup with the case facts does not find {targets} "
                                 f"(found {[r['transaction_id'] for r in found]})")
        except Exception as e:
            rep.err(c["id"], f"lookup failed: {e}")
        for key in (f.get("answers") or {}):
            if key not in slot_ids:
                rep.err(c["id"], f"answer key {key!r} is not a question id in dispute_types.yml")
    conn.close()


# ----------------------------------------------------------------------------- outputs
def export_review(cases: list[dict], fraction: float, seed: int = 7) -> Path:
    rng = random.Random(seed)
    by_cat: dict[str, list[dict]] = {}
    for c in cases:
        by_cat.setdefault(c["category"], []).append(c)
    sample = []
    for cat_cases in by_cat.values():  # stratified, at least one per category
        k = max(1, round(len(cat_cases) * fraction))
        sample += rng.sample(cat_cases, k)
    out = HERE / "review_sample.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "category", "split", "conversation", "gold (yaml)", "reviewer_ok (y/n)", "reviewer_note"])
        import yaml
        for c in sorted(sample, key=lambda x: x["id"]):
            convo = c.get("opening") or " || ".join(t["user"] for t in c.get("turns", []))
            gold = yaml.safe_dump({k: v for k, v in c.items() if k not in ("id", "category", "split")},
                                  sort_keys=False, allow_unicode=True)
            w.writerow([c["id"], c["category"], c["split"], convo, gold, "", ""])
    return out


def freeze(cases: list[dict]) -> Path:
    manifest = {
        "frozen_on": date.today().isoformat(),
        "cases_sha256": hashlib.sha256(CASES_FILE.read_bytes()).hexdigest(),
        "db_sha256": hashlib.sha256(DB_PATH.read_bytes()).hexdigest(),
        "help_documents": len(list(DOCS_DIR.glob("*.md"))),
        "n_cases": len(cases),
        "by_category": dict(Counter(c["category"] for c in cases)),
        "by_split": dict(Counter(c["split"] for c in cases)),
        "review_status": dict(Counter(c.get("review_status", "unreviewed") for c in cases)),
    }
    out = HERE / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", action="store_true", help="write reference results to golden_snapshot.json")
    ap.add_argument("--export-review", type=float, metavar="FRACTION", help="write a stratified review CSV")
    ap.add_argument("--freeze", action="store_true", help="write MANIFEST.json (only if there are no errors)")
    ap.add_argument("--show-sql", action="store_true", help="print every reference result")
    args = ap.parse_args()

    data = load_cases()
    rep = Report()
    cases = check_schema(data, rep)
    snapshot = check_sql(cases, rep)
    check_rag(cases, rep)
    check_disputes(cases, rep, date.today())

    print(f"{len(cases)} cases | by category: {dict(Counter(c['category'] for c in cases))}")
    print(f"split: {dict(Counter(c['split'] for c in cases))} | reference queries: {len(snapshot)}")
    if args.show_sql:
        for k, v in snapshot.items():
            print(f"  {k:12} {v['rows'][:6]}{' ...' if len(v['rows']) > 6 else ''}")
    for w in rep.warnings:
        print("WARN ", w)
    for e in rep.errors:
        print("ERROR", e)
    if args.snapshot:
        (HERE / "golden_snapshot.json").write_text(json.dumps(snapshot, indent=1, default=str), encoding="utf-8")
        print("wrote golden_snapshot.json (relative-date rows are recomputed when scoring)")
    if args.export_review:
        print("wrote", export_review(cases, args.export_review))
    if args.freeze:
        if rep.errors:
            print("not frozen: fix the errors first")
            return 1
        print("wrote", freeze(cases))
    print("OK" if not rep.errors else f"{len(rep.errors)} error(s)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
