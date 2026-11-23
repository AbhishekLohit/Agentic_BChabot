"""
RAG evaluation with two SEPARATE scores.

  1. Retrieval (no LLM):  recall@1/5/10, group recall@k and MRR against the gold
     evidence quotes in the `sbi_rag` cases in evaluation_cases.jsonl.
  2. Answers (LLM):       the app's own help-agent prompt and model answer each
     question from its top-k chunks; a judge LLM then scores
       - correctness   : share of the question's key facts present in the answer
                         (unanswerable questions: did it abstain?)
       - faithfulness  : share of answer sentences supported by the excerpts
       - citation precision: share of citing sentences whose CITED excerpts
                         support them (invalid citation numbers count as wrong)

Reading them together:
  high recall + bad answers  -> the generator (8B model / prompt) is the problem
  low recall                 -> retrieval is the problem; prompt changes won't help

Run from the project root:
  python evaluation/rag/evaluate.py retrieval                    # fast, free, no LLM
  python evaluation/rag/evaluate.py answers --split dev --limit 10
  python evaluation/rag/evaluate.py all --label baseline
  python evaluation/rag/evaluate.py answers --resume evaluation/rag/results/<run folder>
Options: --split dev|test|all, --chunk-size/--chunk-overlap (chunking experiments),
         --judge-provider groq (use a stronger judge than the generator).
"""
import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from validate_eval_set import EVAL_FILE, MANIFEST, load_eval, norm, sha256  # noqa: E402
from utils.help_rag import (CITATION_POLICIES, RETRIEVAL_MODES, cited_numbers,  # noqa: E402
                            enforce_citations, is_abstention, search, split_sentences)

K_VALUES = (1, 5, 10)
RESULTS_DIR = HERE / "results"


# --------------------------------------------------------------------------- setup
def check_freeze(allow_unfrozen: bool) -> str:
    """Refuse to report numbers on an eval set that changed after freezing."""
    if not MANIFEST.is_file():
        print("WARNING: the eval set is not frozen yet (no MANIFEST.json). "
              "Freeze it before recording official numbers:\n"
              "         python evaluation/rag/validate_eval_set.py --freeze\n")
        return "unfrozen"
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if m.get("eval_sha256") != sha256(EVAL_FILE):
        msg = "evaluation_cases.jsonl changed since it was frozen; results would not be comparable."
        if not allow_unfrozen:
            sys.exit(f"ERROR: {msg} (use --allow-unfrozen to run anyway)")
        print(f"WARNING: {msg}\n")
        return "modified"
    return m.get("version", "frozen")


def build_index(chunk_size: Optional[int], chunk_overlap: Optional[int], strategy: Optional[str] = None):
    """Fresh in-memory index built exactly like the app's (same loader, chunker,
    embeddings and cosine space), so every run is reproducible and chunking
    experiments never touch the app's persisted index."""
    import chromadb
    from langchain_chroma import Chroma

    import utils.embeddings.help_document_store as store
    from app_setting import AppSettings
    from clients.llm_client import embedding_model_id, get_embeddings

    if chunk_size:
        store.CHUNK_SIZE = chunk_size
    if chunk_overlap is not None:
        store.CHUNK_OVERLAP = chunk_overlap
    settings = AppSettings()
    strategy = strategy or settings.HELP_CHUNK_STRATEGY
    docs, files = store.load_help_documents(settings.HELP_DOCUMENTS_DIR, strategy)
    embeddings = get_embeddings(settings.EMBEDDING_PROVIDER)
    vs = Chroma(collection_name=f"eval_{uuid.uuid4().hex[:8]}", embedding_function=embeddings,
                client=chromadb.EphemeralClient(), collection_metadata={"hnsw:space": "cosine"})
    t0 = time.time()
    vs.add_documents(docs, ids=[f"d{i}" for i in range(len(docs))])
    info = {"files": len(files), "chunks": len(docs), "chunk_strategy": strategy,
            "avg_chunk_chars": round(sum(len(d.page_content) for d in docs) / max(len(docs), 1)),
            "chunk_size": store.CHUNK_SIZE,
            "chunk_overlap": store.CHUNK_OVERLAP, "embedding_provider": settings.EMBEDDING_PROVIDER,
            "embedding_model": embedding_model_id(embeddings),
            "index_seconds": round(time.time() - t0, 1)}
    print(f"Index: {info['files']} file(s) -> {info['chunks']} chunks, strategy {strategy} "
          f"(size {info['chunk_size']}, overlap {info['chunk_overlap']}, avg {info['avg_chunk_chars']} chars, "
          f"{info['embedding_provider']} embeddings) in {info['index_seconds']}s")
    return vs, info


def load_llms(judge_provider: Optional[str]):
    """Generator = the app's configured LLM (LLM_PROVIDER in .env); judge = same
    provider at temperature 0, or a different provider if --judge-provider is set."""
    from clients.llm_client import get_configured_llm
    generator = get_configured_llm()
    judge = get_configured_llm(provider=judge_provider, temperature=0.0)
    return generator, judge


def load_help_prompt() -> str:
    import yaml
    path = HERE / "evaluation_prompt.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["prompt"]


# --------------------------------------------------------------------------- retrieval
def evidence_ranks(item: dict, texts: list[str]) -> list[Optional[int]]:
    """For each evidence group, the 1-based rank of the first chunk containing any
    of its quotes (None if not retrieved). `texts` must already be norm()-ed."""
    ranks = []
    for group in item["evidence_groups"]:
        quotes = [norm(e["quote"]) for e in group]
        ranks.append(next((i + 1 for i, t in enumerate(texts) if any(q in t for q in quotes)), None))
    return ranks


def retrieval_metrics(ranks: list[Optional[int]]) -> dict:
    full = None if any(r is None for r in ranks) else max(ranks)
    first = min((r for r in ranks if r is not None), default=None)
    out = {"full_rank": full, "first_rank": first, "mrr": 1.0 / first if first else 0.0}
    for k in K_VALUES:
        out[f"recall@{k}"] = float(full is not None and full <= k)
        out[f"group_recall@{k}"] = sum(r is not None and r <= k for r in ranks) / len(ranks)
    return out


def run_retrieval(items: list[dict], vs, mode: Optional[str] = None) -> list[dict]:
    """Rank chunks with the app's own search() in the given mode (default: HELP_RETRIEVAL_MODE)."""
    rows = []
    kmax = max(K_VALUES)
    for item in items:
        if not item["answerable"]:
            continue
        hits = search(vs, item["question"], kmax, mode=mode)
        ranks = evidence_ranks(item, [norm(c["text"]) for c in hits])
        rows.append({"id": item["id"], "split": item["split"], "question_type": item["question_type"],
                     "question": item["question"], "group_ranks": ranks, **retrieval_metrics(ranks),
                     "top_chunks": [{"rank": i + 1, "source": c["metadata"].get("source"),
                                     "chunk": c["metadata"].get("chunk"),
                                     **{k: round(v, 4) for k, v in c.items() if k.endswith("_score")}}
                                    for i, c in enumerate(hits)]})
    return rows


# --------------------------------------------------------------------------- answers
def apply_abstention_rule(row: dict) -> dict:
    """Idempotent: overrides the judge's 'abstained' with the rule when the rule fires.
    A refusal has no factual claims, so it is left out of faithfulness and citation scores."""
    if row.get("error") or "answer" not in row:
        return row
    det = is_abstention(row["answer"])
    row["abstained_by_rule"] = det
    if det:
        row["abstained"] = True
        row["abstained_with_citations"] = any(s["cites"] for s in row.get("sentences", []))
        if row["answerable"]:
            row["key_fact_coverage"], row["all_facts_correct"], row["false_abstention"] = 0.0, 0.0, 1.0
            row["cites_gold_chunk"] = None
        else:
            row["abstention_correct"] = 1.0
        row["faithfulness"] = row["citation_coverage"] = None
        row["n_citing_sentences"] = row["n_citing_supported"] = 0
    return row


class FactVerdict(BaseModel):
    id: int = Field(description="key fact number")
    present: bool = Field(description="the answer states this fact or an equivalent")


class SentenceVerdict(BaseModel):
    id: int = Field(description="sentence number")
    supported: bool = Field(description="every claim in the sentence is supported by the excerpts "
                                        "(true for sentences with no factual claim)")
    cited_support: Optional[bool] = Field(default=None, description="if the sentence cites excerpts: do the "
                                          "CITED excerpts support it? null if it cites nothing")


class JudgeVerdict(BaseModel):
    abstained: bool = Field(description="the answer declines or says the documents do not contain the "
                                        "information, without giving a substantive answer")
    facts: list[FactVerdict] = Field(default_factory=list)
    sentences: list[SentenceVerdict] = Field(default_factory=list)


JUDGE_SYSTEM = """You are a strict, impartial evaluator for a banking help assistant.
You receive a QUESTION, the numbered EXCERPTS the assistant was given (its only allowed evidence),
the assistant's ANSWER split into numbered sentences, and the KEY FACTS a correct answer must contain.

Decide:
1. abstained: true if the answer declines or says the documents do not contain the information,
   without giving a substantive answer.
2. For each key fact: present = true if the answer states it or something equivalent. Wording may
   differ; numbers, limits and conditions must match.
3. For each sentence:
   - supported = true if everything it claims is stated in or directly implied by the EXCERPTS.
     Use only the excerpts, never outside knowledge. A sentence with no factual claim is supported.
   - cited_support = if the sentence cites excerpt numbers like [2]: true only if those CITED
     excerpts themselves support it; false if they do not or the number does not exist.
     null if the sentence cites nothing.
Return one entry for every key fact and every sentence, using their numbers as ids."""


def judge_messages(question: str, contexts: list[dict], sentences: list[str], facts: list[str]) -> list[dict]:
    from utils.help_rag import format_help_context
    body = (f"QUESTION:\n{question}\n\nEXCERPTS:\n{format_help_context(contexts) or '(none)'}\n\n"
            "ANSWER SENTENCES:\n" + "\n".join(f"S{i}: {s}" for i, s in enumerate(sentences, 1)) +
            "\n\nKEY FACTS:\n" + ("\n".join(f"F{i}: {f}" for i, f in enumerate(facts, 1)) or "(none)"))
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": body}]


DEFAULT_OPTS = {"mode": None, "top_k": None, "citation_policy": None, "generation": "structured"}


async def answer_one(item: dict, vs, generator, judge, prompt_text: str, opts: Optional[dict] = None) -> dict:
    """One question through the app's help-agent steps (retrieve -> generate -> citation check),
    then judged. opts: mode, top_k, citation_policy (None = app settings), generation."""
    from app_models import ResponseGeneratorResponse
    from agents.help_agent import NO_DOCUMENTS_MESSAGE
    from utils.help_rag import build_help_messages, retrieve_help_context

    opts = {**DEFAULT_OPTS, **(opts or {})}
    row = {"id": item["id"], "split": item["split"], "question_type": item["question_type"],
           "answerable": item["answerable"], "question": item["question"], "error": None}
    contexts = retrieve_help_context(vs, item["question"], k=opts["top_k"], mode=opts["mode"])
    row["context_chars"] = sum(len(c["text"]) for c in contexts)
    ctx_texts = [norm(c["text"]) for c in contexts]
    row["contexts"] = [{k: c[k] for k in ("n", "source", "page", "chunk", "score")} for c in contexts]
    if item["answerable"]:
        ranks = evidence_ranks(item, ctx_texts)
        row["context_has_all_evidence"] = all(r is not None for r in ranks)
        gold_ns = {r for r in ranks if r is not None}
    else:
        row["context_has_all_evidence"] = None
        gold_ns = set()

    # ---- generate, exactly like the app's help agent
    t0 = time.time()
    if not contexts:
        raw_answer = NO_DOCUMENTS_MESSAGE
    elif opts["generation"] == "plain":  # free-text output, for the structured-output ablation
        msg = await generator.ainvoke(build_help_messages(prompt_text, item["question"], contexts))
        raw_answer = msg.content if isinstance(msg.content, str) else str(msg.content)
    else:
        out = await generator.with_structured_output(ResponseGeneratorResponse, include_raw=True).ainvoke(
            build_help_messages(prompt_text, item["question"], contexts))
        if out.get("parsed") is None:
            raise ValueError(f"generator returned no parsable answer: {str(out.get('raw'))[:200]}")
        raw_answer = out["parsed"].generated_response
    row["generation_seconds"] = round(time.time() - t0, 2)
    answer, report = enforce_citations(raw_answer, len(contexts), opts["citation_policy"])
    row["raw_answer"], row["answer"] = raw_answer, answer
    row["citation_check"] = {"policy": report["policy"], "invalid_citations": report["invalid_citations"],
                             "dropped_sentences": len(report["dropped_sentences"]), "refused": report["refused"]}
    sentences = split_sentences(answer)
    row["sentences"] = [{"text": s, "cites": cited_numbers(s)} for s in sentences]

    # ---- judge
    facts = item["key_facts"] if item["answerable"] else []
    t0 = time.time()
    if answer == NO_DOCUMENTS_MESSAGE or is_abstention(answer):  # refusals need no judge call
        verdict = JudgeVerdict(abstained=True, facts=[FactVerdict(id=i, present=False) for i in range(1, len(facts) + 1)],
                               sentences=[])
        sentences, row["sentences"] = [], []
    else:
        out = await judge.with_structured_output(JudgeVerdict, include_raw=True).ainvoke(
            judge_messages(item["question"], contexts, sentences, facts))
        verdict = out.get("parsed")
        if verdict is None:
            raise ValueError(f"judge returned no parsable verdict: {str(out.get('raw'))[:200]}")
    row["judge_seconds"] = round(time.time() - t0, 2)

    fact_map = {f.id: f.present for f in verdict.facts}
    sent_map = {s.id: s for s in verdict.sentences}
    row["judge_missing_ids"] = ([i for i in range(1, len(facts) + 1) if i not in fact_map] +
                                [f"S{i}" for i in range(1, len(sentences) + 1) if i not in sent_map])
    row["abstained"] = verdict.abstained
    row["facts"] = [{"fact": f, "present": bool(fact_map.get(i, False))} for i, f in enumerate(facts, 1)]

    # correctness
    if item["answerable"]:
        cov = (sum(f["present"] for f in row["facts"]) / len(facts)) if facts else 0.0
        row["key_fact_coverage"] = 0.0 if verdict.abstained else cov
        row["all_facts_correct"] = float(not verdict.abstained and all(f["present"] for f in row["facts"]))
        row["false_abstention"] = float(verdict.abstained)
    else:
        row["abstention_correct"] = float(verdict.abstained)

    # faithfulness + citations
    n_ctx = len(contexts)
    supported = cited = cited_ok = 0
    for i, s in enumerate(row["sentences"], 1):
        v = sent_map.get(i)
        s["supported"] = bool(v.supported) if v else False
        supported += s["supported"]
        if s["cites"]:
            cited += 1
            valid = all(1 <= n <= n_ctx for n in s["cites"])
            s["cited_support"] = bool(valid and v is not None and v.cited_support)
            cited_ok += s["cited_support"]
    n_sent = len(row["sentences"])
    row["faithfulness"] = supported / n_sent if n_sent else None
    row["n_sentences"], row["n_citing_sentences"], row["n_citing_supported"] = n_sent, cited, cited_ok
    row["citation_coverage"] = cited / n_sent if n_sent else None
    all_cites = {n for s in row["sentences"] for n in s["cites"]}
    row["cites_gold_chunk"] = (float(bool(all_cites & gold_ns)) if item["answerable"] and not verdict.abstained
                               else None)
    return apply_abstention_rule(row)


async def run_answers(items: list[dict], vs, generator, judge, out_file: Path, done: set,
                      opts: Optional[dict] = None) -> list[dict]:
    prompt_text = load_help_prompt()
    rows = []
    todo = [it for it in items if it["id"] not in done]
    print(f"Answers: {len(todo)} question(s) to run ({len(done)} already done)")
    for i, item in enumerate(todo, 1):
        t0 = time.time()
        try:
            row = await answer_one(item, vs, generator, judge, prompt_text, opts)
        except Exception as exc:  # keep going; failed items are reported, not silently dropped
            row = {"id": item["id"], "split": item["split"], "question_type": item["question_type"],
                   "answerable": item["answerable"], "question": item["question"],
                   "error": f"{type(exc).__name__}: {exc}"[:500]}
        with out_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        rows.append(row)
        status = "ERROR" if row.get("error") else "ok"
        print(f"  [{i}/{len(todo)}] {item['id']} {status} ({time.time() - t0:.1f}s)", flush=True)
    return rows


# --------------------------------------------------------------------------- summaries
def mean(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def summarize_retrieval(rows: list[dict]) -> dict:
    def block(rs):
        d = {"n": len(rs)}
        for k in K_VALUES:
            d[f"recall@{k}"] = mean(r[f"recall@{k}"] for r in rs)
        for k in K_VALUES:
            d[f"group_recall@{k}"] = mean(r[f"group_recall@{k}"] for r in rs)
        d["mrr"] = mean(r["mrr"] for r in rs)
        return d
    out = {"overall": block(rows)}
    for key in ("split", "question_type"):
        groups = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        out[f"by_{key}"] = {g: block(rs) for g, rs in sorted(groups.items())}
    return out


def summarize_answers(rows: list[dict]) -> dict:
    rows = [apply_abstention_rule(r) for r in rows]
    ok = [r for r in rows if not r.get("error")]
    ans = [r for r in ok if r["answerable"]]
    una = [r for r in ok if not r["answerable"]]
    citing = sum(r["n_citing_sentences"] for r in ok)

    def corr(rs):
        return {"n": len(rs), "key_fact_coverage": mean(r["key_fact_coverage"] for r in rs),
                "all_facts_correct": mean(r["all_facts_correct"] for r in rs)}
    out = {
        "n_total": len(rows), "n_errors": len(rows) - len(ok),
        "correctness": corr(ans),
        "false_abstention_rate": mean(r["false_abstention"] for r in ans),
        "abstention_accuracy_unanswerable": mean(r["abstention_correct"] for r in una),
        "n_unanswerable": len(una),
        "faithfulness": mean(r["faithfulness"] for r in ok),
        "citation_precision": round(sum(r["n_citing_supported"] for r in ok) / citing, 4) if citing else None,
        "citation_coverage": mean(r["citation_coverage"] for r in ok),
        "cites_gold_chunk": mean(r["cites_gold_chunk"] for r in ans),
        "context_recall": mean(float(r["context_has_all_evidence"]) for r in ans),
        # THE diagnostic: answer quality when retrieval succeeded vs failed
        "correctness_when_evidence_retrieved": corr([r for r in ans if r["context_has_all_evidence"]]),
        "correctness_when_evidence_missing": corr([r for r in ans if not r["context_has_all_evidence"]]),
        "judge_missing_ids": sum(bool(r.get("judge_missing_ids")) for r in ok),
        "refusals_with_citations": sum(bool(r.get("abstained_with_citations")) for r in ok),
        "avg_context_chars": mean(r.get("context_chars") for r in ok),
        "avg_generation_seconds": mean(r.get("generation_seconds") for r in ok),
        "invalid_citations_removed": sum((r.get("citation_check") or {}).get("invalid_citations", 0) for r in ok),
        "uncited_sentences_dropped": sum((r.get("citation_check") or {}).get("dropped_sentences", 0) for r in ok),
        "refused_no_support": sum(bool((r.get("citation_check") or {}).get("refused")) for r in ok),
    }
    by_type = defaultdict(list)
    for r in ans:
        by_type[r["question_type"]].append(r)
    out["correctness_by_type"] = {t: corr(rs) for t, rs in sorted(by_type.items())}
    return out


def fmt(v) -> str:
    return "-" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)


def diagnosis(ret: Optional[dict], ansum: Optional[dict]) -> list[str]:
    lines = []
    if ansum and ansum["correctness"]["n"]:
        good = ansum["correctness_when_evidence_retrieved"]
        bad = ansum["correctness_when_evidence_missing"]
        cr = ansum["context_recall"]
        lines.append(f"Generator context contained all gold evidence for {fmt(cr)} of answerable questions.")
        if good["n"]:
            lines.append(f"When the evidence WAS retrieved, key-fact coverage was {fmt(good['key_fact_coverage'])}"
                         f" (n={good['n']}) -> this measures the generator.")
        if bad["n"]:
            lines.append(f"When it was NOT retrieved, coverage was {fmt(bad['key_fact_coverage'])} (n={bad['n']})"
                         f" -> these failures are retrieval's fault.")
        if cr is not None and good["key_fact_coverage"] is not None:
            if cr >= 0.8 and good["key_fact_coverage"] < 0.7:
                lines.append("Verdict: retrieval is mostly fine; focus on the GENERATOR (prompt, context size, model).")
            elif cr < 0.8:
                lines.append("Verdict: RETRIEVAL is losing evidence; improve chunking / hybrid search / reranking first.")
            else:
                lines.append("Verdict: both stages are performing well on this set.")
    elif ret:
        r5 = ret["overall"].get("recall@5")
        lines.append(f"Retrieval recall@5 = {fmt(r5)}. Run the 'answers' stage to see whether the generator "
                     "uses the evidence it gets.")
    return lines


def write_report(run_dir: Path, meta: dict, ret: Optional[dict], ansum: Optional[dict]) -> str:
    md = [f"# RAG evaluation: {meta['label']}", "",
          f"- Run: {meta['run_at']}  |  eval set: {meta['eval_version']}  |  split: {meta['split']}",
          f"- Index: {meta.get('index', {})}",
          f"- Options: {meta.get('options', {})}"]
    if meta.get("generator"):
        md.append(f"- Generator: {meta['generator']}  |  judge: {meta['judge']}")
    if ret:
        md += ["", "## Retrieval (no LLM)", "",
               "| Slice | n | " + " | ".join(f"R@{k}" for k in K_VALUES) + " | MRR | " +
               " | ".join(f"groupR@{k}" for k in K_VALUES) + " |",
               "|---|---|" + "---|" * (2 * len(K_VALUES) + 1)]
        def line(name, d):
            return (f"| {name} | {d['n']} | " + " | ".join(fmt(d[f'recall@{k}']) for k in K_VALUES) +
                    f" | {fmt(d['mrr'])} | " + " | ".join(fmt(d[f'group_recall@{k}']) for k in K_VALUES) + " |")
        md.append(line("**overall**", ret["overall"]))
        for name, d in ret["by_split"].items():
            md.append(line(f"split: {name}", d))
        for name, d in ret["by_question_type"].items():
            md.append(line(name, d))
    if ansum:
        c = ansum["correctness"]
        md += ["", "## Answers (LLM-judged)", "",
               "| Metric | Value |", "|---|---|",
               f"| Key-fact coverage (answerable, n={c['n']}) | {fmt(c['key_fact_coverage'])} |",
               f"| All key facts correct | {fmt(c['all_facts_correct'])} |",
               f"| Faithfulness (sentences supported by excerpts) | {fmt(ansum['faithfulness'])} |",
               f"| Citation precision (citing sentences supported by their cited excerpts) | {fmt(ansum['citation_precision'])} |",
               f"| Citation coverage (sentences that cite something) | {fmt(ansum['citation_coverage'])} |",
               f"| Answers citing a gold-evidence chunk | {fmt(ansum['cites_gold_chunk'])} |",
               f"| Abstention accuracy (unanswerable, n={ansum['n_unanswerable']}) | {fmt(ansum['abstention_accuracy_unanswerable'])} |",
               f"| False abstention (answerable but declined) | {fmt(ansum['false_abstention_rate'])} |",
               f"| Refusals that still cited excerpts (prompt violation) | {ansum.get('refusals_with_citations', 0)} |",
               f"| Avg context given to the LLM (chars) | {fmt(ansum.get('avg_context_chars'))} |",
               f"| Citation check: invalid citations removed / uncited sentences dropped / refused | "
               f"{ansum.get('invalid_citations_removed', 0)} / {ansum.get('uncited_sentences_dropped', 0)} / "
               f"{ansum.get('refused_no_support', 0)} |",
               f"| Errors | {ansum['n_errors']} of {ansum['n_total']} |",
               "", "### Retrieval vs generator", "",
               "| Generator's context had all gold evidence? | n | Key-fact coverage | All facts correct |",
               "|---|---|---|---|"]
        for name, key in (("yes", "correctness_when_evidence_retrieved"), ("no", "correctness_when_evidence_missing")):
            d = ansum[key]
            md.append(f"| {name} | {d['n']} | {fmt(d['key_fact_coverage'])} | {fmt(d['all_facts_correct'])} |")
        md += ["", "### Correctness by question type", "", "| Type | n | Key-fact coverage | All facts correct |",
               "|---|---|---|---|"]
        for t, d in ansum["correctness_by_type"].items():
            md.append(f"| {t} | {d['n']} | {fmt(d['key_fact_coverage'])} | {fmt(d['all_facts_correct'])} |")
    md += ["", "## Diagnosis", ""] + [f"- {l}" for l in diagnosis(ret, ansum)]
    text = "\n".join(md) + "\n"
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({"meta": meta, "retrieval": ret, "answers": ansum},
                                                     indent=2), encoding="utf-8")
    return text


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate retrieval and answers separately.")
    ap.add_argument("stage", choices=["retrieval", "answers", "all", "rescore"],
                    help="rescore = rebuild report.md from a saved run (--resume DIR), no LLM calls")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev",
                    help="tune on dev; report test once at the end (default: dev)")
    ap.add_argument("--limit", type=int, help="only the first N questions (quick checks)")
    ap.add_argument("--label", default=None, help="name for this run, e.g. baseline or chunk800")
    ap.add_argument("--chunk-size", type=int, help="override chunk size (chunking experiments)")
    ap.add_argument("--chunk-overlap", type=int, help="override chunk overlap")
    ap.add_argument("--chunk-strategy", choices=["fixed", "structure", "structure_header"],
                    help="override the chunking strategy (default: HELP_CHUNK_STRATEGY)")
    ap.add_argument("--judge-provider", help="judge with a different LLM provider (e.g. groq)")
    ap.add_argument("--retrieval-mode", choices=list(RETRIEVAL_MODES),
                    help="dense, bm25, hybrid or hybrid_rerank (default: HELP_RETRIEVAL_MODE)")
    ap.add_argument("--top-k", type=int, help="chunks given to the LLM (default: HELP_TOP_K)")
    ap.add_argument("--citation-policy", choices=list(CITATION_POLICIES),
                    help="off, check or strict (default: HELP_CITATION_POLICY)")
    ap.add_argument("--generation", choices=["structured", "plain"], default="structured",
                    help="structured JSON output (as the app) or plain text (ablation)")
    ap.add_argument("--resume", help="continue an interrupted answers run in this folder")
    ap.add_argument("--allow-unfrozen", action="store_true")
    args = ap.parse_args()

    if args.stage == "rescore":
        if not args.resume:
            sys.exit("ERROR: rescore needs --resume evaluation/rag/results/<run folder>")
        run_dir = Path(args.resume)
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        ret = ansum = None
        if (run_dir / "retrieval.jsonl").is_file():
            ret = summarize_retrieval([json.loads(l) for l in (run_dir / "retrieval.jsonl").open(encoding="utf-8")])
        if (run_dir / "answers.jsonl").is_file():
            ansum = summarize_answers([json.loads(l) for l in (run_dir / "answers.jsonl").open(encoding="utf-8")])
        if (run_dir / "report.md").is_file() and not (run_dir / "report_before_rescore.md").is_file():
            (run_dir / "report.md").replace(run_dir / "report_before_rescore.md")
        meta["rescored_at"] = datetime.now().isoformat(timespec="seconds")
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(write_report(run_dir, meta, ret, ansum))
        print(f"Rescored {run_dir} (no LLM calls). The previous report is report_before_rescore.md")
        return 0

    version = check_freeze(args.allow_unfrozen)
    if args.resume:  # repeat the original run's settings exactly
        run_dir = Path(args.resume)
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        args.split, args.limit = meta["split"], meta.get("limit")
        args.chunk_size, args.chunk_overlap = meta["index"]["chunk_size"], meta["index"]["chunk_overlap"]
        args.chunk_strategy = meta["index"].get("chunk_strategy")
        args.judge_provider = meta.get("judge_provider")
        for key in ("retrieval_mode", "top_k", "citation_policy", "generation"):
            if key in meta.get("options", {}):
                setattr(args, key, meta["options"][key])
    items = [it for it in load_eval() if args.split == "all" or it["split"] == args.split]
    if args.limit:
        items = items[: args.limit]

    if not args.resume:
        label = args.label or args.stage
        run_dir = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{re.sub(r'[^A-Za-z0-9_-]', '_', label)}"
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = {"label": label, "run_at": datetime.now().isoformat(timespec="seconds"), "stage": args.stage,
                "split": args.split, "limit": args.limit, "eval_version": version}
        from app_setting import AppSettings
        s0 = AppSettings()
        meta["options"] = {"retrieval_mode": args.retrieval_mode or s0.HELP_RETRIEVAL_MODE,
                           "top_k": args.top_k or s0.HELP_TOP_K,
                           "citation_policy": args.citation_policy or s0.HELP_CITATION_POLICY,
                           "generation": args.generation}

    vs, meta["index"] = build_index(args.chunk_size, args.chunk_overlap, args.chunk_strategy)
    ret = ansum = None

    if args.stage in ("retrieval", "all") and not args.resume:
        rows = run_retrieval(items, vs, mode=args.retrieval_mode)
        with (run_dir / "retrieval.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        ret = summarize_retrieval(rows)
    elif (run_dir / "retrieval.jsonl").is_file():
        ret = summarize_retrieval([json.loads(l) for l in (run_dir / "retrieval.jsonl").open(encoding="utf-8")])

    if args.stage in ("answers", "all") or args.resume:
        from app_setting import AppSettings
        s = AppSettings()
        generator, judge = load_llms(args.judge_provider)
        meta["generator"] = f"{s.LLM_PROVIDER}"
        meta["judge"] = f"{args.judge_provider or s.LLM_PROVIDER} (temperature 0)"
        meta["judge_provider"] = args.judge_provider
        out_file = run_dir / "answers.jsonl"
        done = set()
        if out_file.is_file():
            prev = [json.loads(l) for l in out_file.open(encoding="utf-8")]
            ok_prev = [r for r in prev if not r.get("error")]
            done = {r["id"] for r in ok_prev}
            out_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ok_prev), encoding="utf-8")
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        opts = {"mode": args.retrieval_mode, "top_k": args.top_k,
                "citation_policy": args.citation_policy, "generation": args.generation}
        asyncio.run(run_answers(items, vs, generator, judge, out_file, done, opts))
        ansum = summarize_answers([json.loads(l) for l in out_file.open(encoding="utf-8")])

    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("\n" + write_report(run_dir, meta, ret, ansum))
    print(f"Saved to {run_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
