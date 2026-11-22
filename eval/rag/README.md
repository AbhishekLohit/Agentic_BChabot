# RAG evaluation set

A fixed set of questions for measuring the help-document RAG pipeline. It scores **retrieval**
(did we fetch the right evidence?) separately from **answers** (did the model use it correctly?),
so you can tell whether a bad answer came from retrieval or from the 8B generator.

## Files

| File | What it is |
|---|---|
| `rag_eval_set.jsonl` | The eval set, one question per line. **This is the source of truth.** |
| `rag_eval_set.csv` | The same data for reading and reviewing in Excel. Generated; don't edit. |
| `validate_eval_set.py` | Checks every evidence quote against the corpus, and freezes the set. |
| `MANIFEST.json` | Written by `--freeze`: hashes of the eval set and corpus, counts, rules. |

## What's in it (v1)

102 questions over `data/help_documents/SBI_Help_QA.pdf` (44 Q&As, 7 sections).

| Type | Count | Tests |
|---|---|---|
| `paraphrase` | 23 | Different wording from the document (favours dense retrieval) |
| `factual` | 15 | Numbers and limits (Rs. 5,00,000, T+5, 1930) |
| `procedural` | 15 | Steps and menu paths |
| `reasoning` | 12 | Applying a rule to the user's situation ("my son is 12…") |
| `keyword` | 8 | Exact terms: PPK, Green PIN, UPI Lite, NREGA, AnyDesk (favours BM25) |
| `near_miss` | 7 | A related section exists, but the answer is "no" or "only at a branch" |
| `multi_section` | 7 | Needs evidence from two different Q&As |
| `unanswerable` | 15 | Bank questions the corpus does **not** cover; the right behaviour is to abstain |

Split: **70 dev / 32 test**, stratified by type. Tune on `dev`; report final numbers on `test`.

## Schema

```json
{
  "id": "E038",
  "question": "Why can't I send more than 5 lakh to a beneficiary I just added?",
  "question_type": "paraphrase",
  "answerable": true,
  "expected_behavior": "answer_with_citations",
  "split": "dev",
  "gold_doc": "SBI_Help_QA.pdf",
  "evidence_groups": [
    [ {"section": "Q4.2", "page": 4, "quote": "first four days after a beneficiary is activated"} ]
  ],
  "reference_answer": "For the first four days after a beneficiary is activated ...",
  "key_facts": ["first four days after activation", "capped at Rs. 5,00,000", "cooling period for protection"],
  "notes": ""
}
```

- **`evidence_groups`** is a list of groups. A **group** is satisfied if *any* of its quotes is retrieved
  (some facts appear in two places). A question is **fully recalled** when *every* group is satisfied
  (multi-section questions have two groups).
- **`quote`** is a short verbatim span from the document. A retrieved chunk is a hit when it contains
  the quote (case-, whitespace- and quote-style-insensitive).
- **`key_facts`** are the points a correct answer must contain; use them for answer scoring.

### Why quotes instead of chunk IDs?

Chunk IDs change every time you change the chunking. Quotes don't, so this one eval set scores every
chunking experiment fairly. The validator also warns if the current chunker splits a quote across two
chunks, since that caps recall.

## Running the evaluation (`evaluate.py`)

Two **separate** scores, so you can tell which stage failed:

| Stage | Command | Needs an LLM? | Metrics |
|---|---|---|---|
| Retrieval | `python eval/rag/evaluate.py retrieval` | No (seconds, free) | recall@1/5/10, group recall@k, MRR |
| Answers | `python eval/rag/evaluate.py answers` | Yes (generator + judge) | correctness, faithfulness, citation precision, abstention |
| Both | `python eval/rag/evaluate.py all --label baseline` | Yes | everything, in one report |

Useful options:

| Option | What it does |
|---|---|
| `--split dev` / `test` / `all` | Default `dev`. Tune on dev; run `test` once for the final report. |
| `--limit 5` | Only the first N questions (quick smoke test). |
| `--label baseline` | Names the run folder. |
| `--chunk-size 800 --chunk-overlap 100` | Chunking experiments. Builds a fresh in-memory index; the app's index is untouched. |
| `--judge-provider groq` | Judge with a different (stronger) model than the generator. |
| `--resume eval/rag/results/<folder>` | Continue an interrupted answers run with the same settings; failed questions are retried. |

Each run writes a folder under `eval/rag/results/` with `report.md` (tables), `summary.json`,
`retrieval.jsonl` and `answers.jsonl` (one row per question, for error analysis), and `meta.json`.

### How each metric is computed

**Retrieval** (answerable questions; the index is built exactly like the app's):
- A retrieved chunk is a hit for an evidence group if it contains one of the group's quotes.
- **recall@k**: share of questions whose every evidence group is hit within the top k.
- **group recall@k**: share of evidence groups hit within the top k (partial credit for multi-section).
- **MRR**: mean of 1 / rank of the first hit (0 if none in the top 10).

**Answers** (the app's own help-agent prompt, retrieval (top 4) and LLM; a judge LLM at temperature 0):
- **Key-fact coverage** (correctness): share of the question's `key_facts` present in the answer.
  **All facts correct** is the strict version. Declining an answerable question scores 0.
- **Abstention accuracy**: on unanswerable questions, share where the system declined.
- **Faithfulness**: share of answer sentences fully supported by the retrieved excerpts.
- **Citation precision**: share of sentences citing `[n]` whose *cited* excerpts support them.
  A citation to an excerpt number that doesn't exist counts as wrong.
- **Citation coverage**: share of sentences that cite something.
- **Answers citing a gold chunk**: deterministic check (no judge) that the answer cites an excerpt
  containing the gold evidence.

### The diagnostic

The report splits answer correctness by whether the generator's context contained all the gold evidence:

| Context had the evidence? | Low correctness means |
|---|---|
| Yes | The **generator** failed: prompt, context size, or the 8B model |
| No | **Retrieval** failed; fix chunking, hybrid search or reranking first |

### Caveats to state in your report

- The judge is an LLM. If it is the same 8B model as the generator, it may be lenient with its own
  mistakes. Use `--judge-provider` with a stronger model if you can, and hand-check about 20 judged
  answers (from `answers.jsonl`) to report judge agreement.
- The capstone API allows one active request per group key, so the answers stage runs sequentially
  (two calls per question). Expect several minutes for the dev split; use `--resume` if interrupted.

## Chunking experiments (`chunking_experiments.py`)

Runs the retrieval evaluation (no LLM) for six chunking setups and puts them side by side:
fixed windows of 500, 1,000, 1,200 (current) and 1,500 characters, `structure` (one chunk per
Q&A, page headers removed) and `structure_header` (the same plus "document > section" on each chunk).

```powershell
python eval/rag/chunking_experiments.py               # dev split: use this to choose
python eval/rag/chunking_experiments.py --split test  # once, to confirm the choice
```

The app's strategy is set by `HELP_CHUNK_STRATEGY` in `.env` (`fixed`, `structure` or
`structure_header`; default `fixed`). After changing it, rebuild the index
(`cd src`, then `python -m utils.embeddings.help_document_store`). To check answer quality with a
strategy before switching: `python eval/rag/evaluate.py all --chunk-strategy structure_header --label structure_header`.

Note: recall@1 on `multi_section` questions is 0 by design for one-chunk-per-Q&A strategies, because no
single chunk contains two Q&As. Compare those questions with group recall@k instead.

## Retrieval modes: dense vs BM25 vs hybrid vs reranker (`retrieval_experiments.py`)

No LLM is needed. Every mode ranks chunks from the same index with the app's own `search()` in
`src/utils/help_rag.py`:

| Mode | What it does |
|---|---|
| `dense` | embedding similarity only (the original behaviour) |
| `bm25` | keyword search only (`rank_bm25`) |
| `hybrid` | dense + BM25 fused with reciprocal rank fusion (k = 60) |
| `hybrid_rerank` | the hybrid top 30 re-scored by a cross-encoder (flashrank `ms-marco-MiniLM-L-12-v2`, ONNX, no PyTorch), best 4 kept |

    python eval/rag/retrieval_experiments.py --split dev
    python eval/rag/retrieval_experiments.py --split dev --chunk-strategy structure_header

The first `hybrid_rerank` run downloads the reranker model (about 30 MB) to `~/.cache/flashrank`.
`evaluate.py retrieval --retrieval-mode hybrid` gives the per-question view for one mode.

## Answer-side options in `evaluate.py`

| Flag | Values | Default |
|---|---|---|
| `--retrieval-mode` | dense, bm25, hybrid, hybrid_rerank | `HELP_RETRIEVAL_MODE` |
| `--top-k` | chunks given to the LLM | `HELP_TOP_K` (4) |
| `--citation-policy` | off, check, strict | `HELP_CITATION_POLICY` (strict) |
| `--generation` | structured, plain | structured |

Each answer row keeps `raw_answer` (what the LLM wrote), `answer` (after the citation check),
`citation_check` and `context_chars`. The judge scores the final answer, which is what the user sees.

Citation policies (`enforce_citations` in `help_rag.py`, also used by the help agent):
- `off`: pass the answer through.
- `check`: remove citation numbers that don't exist; refuse if nothing valid is cited.
- `strict`: also drop every sentence without a valid citation; refuse if nothing is left.

## Small-model ablation (`ablation.py`)

Six rungs; each adds one change to the rung before it, so each step's difference is that change alone:

| Rung | Change |
|---|---|
| 0 | Baseline: dense top-8, plain text, no citation check |
| 1 | + structured output |
| 2 | + shorter context (top-4) |
| 3 | + hybrid search |
| 4 | + reranker |
| 5 | + strict citations / abstaining |

    python eval/rag/ablation.py --split dev --rungs 0 5 --limit 3   # smoke test
    python eval/rag/ablation.py --split dev                         # full ladder
    python eval/rag/ablation.py --resume eval/rag/results/<time>_ablation   # after an interruption

Each run writes `report.md` (the before-and-after table) and `small_model_section.md`, a draft of the
"compensating for a small model" write-up filled from the measured numbers.

## Commands (run from the project root)

```powershell
python eval/rag/validate_eval_set.py            # verify evidence + check the freeze
python eval/rag/validate_eval_set.py --freeze   # freeze: record hashes in MANIFEST.json
```

## Rules

1. Review the questions, fix anything wrong, then **freeze before running experiments**.
2. After freezing, **don't edit** questions, evidence or answers, especially on `test`. If you find a
   mistake, fix it, bump `version` in `MANIFEST.json` (v1 → v2), re-freeze, and note it in your report.
3. When the corpus grows toward 500–2,000 documents, add questions for the new documents (keep the
   same type mix and roughly 1/3 test), validate, and freeze as a new version. Record a fresh baseline
   for every version.
