# RAG evaluation: smoke

- Run: 2026-11-18T00:49:46  |  eval set: unfrozen  |  split: dev
- Index: {'files': 687, 'chunks': 800, 'chunk_strategy': 'fixed', 'avg_chunk_chars': 825, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'capstone', 'embedding_model': 'capstone:bge-small-en-v1.5', 'index_seconds': 42.8}
- Options: {'retrieval_mode': 'dense', 'top_k': 4, 'citation_policy': 'strict', 'generation': 'structured'}
- Generator: capstone  |  judge: capstone (temperature 0)

## Retrieval (no LLM)

| Slice | n | R@1 | R@5 | R@10 | MRR | groupR@1 | groupR@5 | groupR@10 |
|---|---|---|---|---|---|---|---|---|
| **overall** | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| split: dev | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| factual | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| paraphrase | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| reasoning | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Answers (LLM-judged)

| Metric | Value |
|---|---|
| Key-fact coverage (answerable, n=3) | 1.000 |
| All key facts correct | 1.000 |
| Faithfulness (sentences supported by excerpts) | 1.000 |
| Citation precision (citing sentences supported by their cited excerpts) | 1.000 |
| Citation coverage (sentences that cite something) | 1.000 |
| Answers citing a gold-evidence chunk | 1.000 |
| Abstention accuracy (unanswerable, n=0) | - |
| False abstention (answerable but declined) | 0.000 |
| Refusals that still cited excerpts (prompt violation) | 0 |
| Avg context given to the LLM (chars) | 3513.667 |
| Citation check: invalid citations removed / uncited sentences dropped / refused | 0 / 5 / 0 |
| Errors | 0 of 3 |

### Retrieval vs generator

| Generator's context had all gold evidence? | n | Key-fact coverage | All facts correct |
|---|---|---|---|
| yes | 3 | 1.000 | 1.000 |
| no | 0 | - | - |

### Correctness by question type

| Type | n | Key-fact coverage | All facts correct |
|---|---|---|---|
| factual | 1 | 1.000 | 1.000 |
| paraphrase | 1 | 1.000 | 1.000 |
| reasoning | 1 | 1.000 | 1.000 |

## Diagnosis

- Generator context contained all gold evidence for 1.000 of answerable questions.
- When the evidence WAS retrieved, key-fact coverage was 1.000 (n=3) -> this measures the generator.
- Verdict: both stages are performing well on this set.
