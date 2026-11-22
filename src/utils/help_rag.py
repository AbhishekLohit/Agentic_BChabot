"""
Shared help-document RAG steps, used by the help agent AND by the evaluator
(eval/rag/*), so every evaluation measures exactly what the app does.

Retrieval modes (HELP_RETRIEVAL_MODE in .env):
  dense          - embedding similarity search (Chroma)
  bm25           - keyword search (rank_bm25 over the same chunks)
  hybrid         - dense + BM25 fused with reciprocal rank fusion (RRF)
  hybrid_rerank  - hybrid candidates re-scored by a cross-encoder (flashrank, ONNX)

Citations (HELP_CITATION_POLICY): excerpts are numbered [1]..[n]; after generation
  off     - answer passed through unchanged
  check   - citations to excerpts that don't exist are removed; refuse if nothing valid is cited
  strict  - as check, and every sentence without a valid citation is removed
"""
import hashlib
import os
import re
from typing import Any, Callable, Optional

DEFAULT_TOP_K = 4
MIN_RELEVANCE = 0.0          # dense mode: chunks scoring at or below this are dropped
RRF_K = 60                   # standard reciprocal-rank-fusion constant
RETRIEVAL_MODES = ("dense", "bm25", "hybrid", "hybrid_rerank")
CITATION_POLICIES = ("off", "check", "strict")
NO_SUPPORT_MESSAGE = ("The available help documents do not provide enough information to answer this "
                      "question reliably.")


def _settings():
    from app_setting import AppSettings
    return AppSettings()


# --------------------------------------------------------------------------- tokenising (BM25)
_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")
_STOPWORDS = set("""a an and are as at be but by can do does for from has have how i if in into is it its
me my no not of on or our so than that the their them then there these they this to was we were what
when where which who why will with you your""".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


# --------------------------------------------------------------------------- retrievers
def _key(doc_id: Optional[str], text: str) -> str:
    return doc_id or hashlib.sha1(text.encode("utf-8")).hexdigest()


def _candidate(key: str, text: str, metadata: dict) -> dict:
    return {"key": key, "text": text, "metadata": metadata or {}}


def dense_search(vector_store: Any, query: str, k: int) -> list[dict]:
    out = []
    for doc, score in vector_store.similarity_search_with_relevance_scores(query, k=k):
        c = _candidate(_key(getattr(doc, "id", None), doc.page_content), doc.page_content, doc.metadata)
        c["dense_score"] = float(score)
        out.append(c)
    return out


class _Bm25Index:
    def __init__(self, ids: list, texts: list[str], metadatas: list[dict]) -> None:
        from rank_bm25 import BM25Okapi

        self.ids, self.texts, self.metadatas = ids, texts, metadatas
        self.bm25 = BM25Okapi([tokenize(t) or ["_"] for t in texts])

    def search(self, query: str, k: int) -> list[dict]:
        scores = self.bm25.get_scores(tokenize(query) or ["_"])
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        out = []
        for i in order:
            if scores[i] <= 0:
                break
            c = _candidate(_key(self.ids[i], self.texts[i]), self.texts[i], self.metadatas[i])
            c["bm25_score"] = float(scores[i])
            out.append(c)
        return out


_BM25_CACHE: dict = {}


def bm25_index(vector_store: Any) -> _Bm25Index:
    """BM25 over exactly the chunks stored in the vector store; rebuilt when the index changes."""
    collection = vector_store._collection
    key = (collection.id, collection.count())
    if key not in _BM25_CACHE:
        data = vector_store.get(include=["documents", "metadatas"])
        _BM25_CACHE.clear()
        _BM25_CACHE[key] = _Bm25Index(data["ids"], data["documents"], data["metadatas"])
    return _BM25_CACHE[key]


def bm25_search(vector_store: Any, query: str, k: int) -> list[dict]:
    if not vector_store._collection.count():  # empty index: nothing to search
        return []
    return bm25_index(vector_store).search(query, k)


def rrf_fuse(*ranked_lists: list[dict], k: int = RRF_K) -> list[dict]:
    """Reciprocal rank fusion: score = sum over lists of 1 / (k + rank)."""
    fused: dict[str, dict] = {}
    for results in ranked_lists:
        for rank, c in enumerate(results, 1):
            entry = fused.setdefault(c["key"], {**c, "rrf_score": 0.0})
            entry.update({kk: v for kk, v in c.items() if kk.endswith("_score")})
            entry["rrf_score"] += 1.0 / (k + rank)
    return sorted(fused.values(), key=lambda c: -c["rrf_score"])


# ---- reranker (cross-encoder). Swappable for tests via set_reranker().
_RERANKERS: dict = {}
_RERANK_OVERRIDE: Optional[Callable[[str, list[str]], list[float]]] = None


def set_reranker(fn: Optional[Callable[[str, list[str]], list[float]]]) -> None:
    """Replace the cross-encoder with fn(query, passages) -> scores (tests/experiments)."""
    global _RERANK_OVERRIDE
    _RERANK_OVERRIDE = fn


def _flashrank_scores(query: str, passages: list[str], model_name: str) -> list[float]:
    from flashrank import Ranker, RerankRequest

    if model_name not in _RERANKERS:
        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "flashrank")
        os.makedirs(cache_dir, exist_ok=True)
        _RERANKERS[model_name] = Ranker(model_name=model_name, cache_dir=cache_dir, log_level="WARNING")
    results = _RERANKERS[model_name].rerank(
        RerankRequest(query=query, passages=[{"id": i, "text": p} for i, p in enumerate(passages)]))
    scores = [0.0] * len(passages)
    for r in results:
        scores[int(r["id"])] = float(r["score"])
    return scores


def rerank(query: str, candidates: list[dict], model_name: str) -> list[dict]:
    if not candidates:
        return []
    passages = [c["text"] for c in candidates]
    scores = (_RERANK_OVERRIDE(query, passages) if _RERANK_OVERRIDE
              else _flashrank_scores(query, passages, model_name))
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    return sorted(candidates, key=lambda c: -c["rerank_score"])


def search(vector_store: Any, query: str, k: int, mode: Optional[str] = None,
           candidates: Optional[int] = None, reranker_model: Optional[str] = None) -> list[dict]:
    """Ranked chunks for a query (best first), using the given or configured retrieval mode."""
    if mode is None or candidates is None or reranker_model is None:
        s = _settings()
        mode = mode or s.HELP_RETRIEVAL_MODE
        candidates = candidates or s.HELP_RETRIEVAL_CANDIDATES
        reranker_model = reranker_model or s.RERANKER_MODEL
    mode = mode.lower().strip()
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"Unknown retrieval mode {mode!r}; use one of {RETRIEVAL_MODES}")
    n = max(k, candidates)
    if mode == "dense":
        return [c for c in dense_search(vector_store, query, k) if c["dense_score"] > MIN_RELEVANCE]
    if mode == "bm25":
        return bm25_search(vector_store, query, k)
    fused = rrf_fuse(dense_search(vector_store, query, n), bm25_search(vector_store, query, n))
    if mode == "hybrid":
        return fused[:k]
    return rerank(query, fused[:n], reranker_model)[:k]


def retrieve_help_context(vector_store: Any, query: str, k: Optional[int] = None,
                          mode: Optional[str] = None) -> list[dict]:
    """Top-k chunks for the query, numbered [1]..[n] in rank order (what the LLM sees)."""
    k = k or _settings().HELP_TOP_K
    contexts = []
    for c in search(vector_store, query, k, mode=mode):
        md = c["metadata"]
        score = next((c[s] for s in ("rerank_score", "rrf_score", "dense_score", "bm25_score") if s in c), None)
        contexts.append({"n": len(contexts) + 1, "text": c["text"], "source": md.get("source", "unknown"),
                         "page": md.get("page"), "chunk": md.get("chunk"),
                         "score": float(score) if score is not None else None})
    return contexts


def format_help_context(contexts: list[dict]) -> str:
    parts = []
    for c in contexts:
        where = f"{c['source']}, page {c['page']}" if c.get("page") else c["source"]
        parts.append(f"[{c['n']}] (Source: {where})\n{c['text']}")
    return "\n\n".join(parts)


def build_help_messages(prompt_text: str, query: str, contexts: list[dict]) -> list[dict]:
    return [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": (
            f"Question: {query}\n\n"
            f"Retrieved document excerpts (cite them by number):\n"
            f"{format_help_context(contexts)}"
        )},
    ]


# --------------------------------------------------------------------------- answers & citations
CITE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
LEADING_CITES = re.compile(r"^((?:\s*\[\d+(?:\s*[,;]\s*\d+)*\])+)")
ABBREVIATIONS = ("rs.", "e.g.", "i.e.", "no.", "nos.", "etc.", "vs.", "approx.", "mr.", "mrs.", "ms.",
                 "dr.", "st.", "a/c.", "ltd.", "pvt.", "inr.")
# The help prompt tells the model exactly how to decline, so refusals are detected by rule.
ABSTAIN = re.compile(r"(help )?documents? (do|does) not (provide|contain|cover|include|mention|specify)"
                     r"|could ?n[o']t find (any )?information|(is|are) not (covered|available|mentioned) in the "
                     r"(available )?(help )?documents|no information (about|on) (this|that) in the", re.I)


def split_sentences(text: str) -> list[str]:
    raw = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip()) if p and p.strip()]
    pieces: list[str] = []
    for p in raw:  # re-join pieces split after an abbreviation such as "Rs."
        if pieces and pieces[-1].lower().endswith(ABBREVIATIONS):
            pieces[-1] += " " + p
        else:
            pieces.append(p)
    sentences: list[str] = []
    for p in pieces:
        m = LEADING_CITES.match(p)  # "...sentence. [2]" -> attach "[2]" to the previous sentence
        if m and sentences:
            sentences[-1] += " " + m.group(1).strip()
            p = p[m.end():].strip()
        if p and CITE.sub("", p).strip(" .,;:-*") != "":
            sentences.append(p)
    return sentences


def cited_numbers(sentence: str) -> list[int]:
    return sorted({int(n) for grp in CITE.findall(sentence) for n in re.split(r"[,;]", grp)})


def is_abstention(answer: str) -> bool:
    """A refusal: opens with the prescribed 'documents do not provide' wording, at most 2 sentences."""
    sentences = split_sentences(answer or "")
    return bool(sentences) and bool(ABSTAIN.search(sentences[0])) and len(sentences) <= 2


def _keep_valid_citations(sentence: str, n_contexts: int) -> tuple[str, int, int]:
    """Rewrite [..] markers keeping only numbers 1..n_contexts. Returns (text, n_valid, n_invalid)."""
    counts = {"valid": 0, "invalid": 0}

    def fix(m):
        nums = [int(x) for x in re.split(r"\s*[,;]\s*", m.group(1))]
        good = [x for x in nums if 1 <= x <= n_contexts]
        counts["valid"] += len(good)
        counts["invalid"] += len(nums) - len(good)
        return "".join(f"[{x}]" for x in dict.fromkeys(good))

    text = CITE.sub(fix, sentence)
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip(), counts["valid"], counts["invalid"]


def enforce_citations(answer: str, n_contexts: int, policy: Optional[str] = None) -> tuple[str, dict]:
    """Post-check the generated answer against the numbered excerpts it was given.
    Returns (final_answer, report)."""
    policy = (policy or _settings().HELP_CITATION_POLICY).lower().strip()
    if policy not in CITATION_POLICIES:
        raise ValueError(f"Unknown citation policy {policy!r}; use one of {CITATION_POLICIES}")
    report = {"policy": policy, "invalid_citations": 0, "dropped_sentences": [], "refused": False,
              "abstained": False}
    if policy == "off":
        return answer, report
    if is_abstention(answer):  # a refusal needs no citations; strip any it carries
        report["abstained"] = True
        return CITE.sub("", answer).replace(" .", ".").strip(), report
    kept, any_valid = [], False
    for sentence in split_sentences(answer):
        fixed, n_valid, n_invalid = _keep_valid_citations(sentence, n_contexts)
        report["invalid_citations"] += n_invalid
        if n_valid:
            any_valid = True
        elif policy == "strict":
            report["dropped_sentences"].append(sentence)
            continue
        kept.append(fixed)
    if not any_valid:
        report["refused"] = True
        return NO_SUPPORT_MESSAGE, report
    return "\n".join(kept) if "\n" in (answer or "") else " ".join(kept), report
