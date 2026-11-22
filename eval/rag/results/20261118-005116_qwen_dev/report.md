# RAG evaluation: qwen_dev

- Run: 2026-11-18T00:51:16  |  eval set: unfrozen  |  split: dev
- Index: {'files': 687, 'chunks': 800, 'chunk_strategy': 'fixed', 'avg_chunk_chars': 825, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'capstone', 'embedding_model': 'capstone:bge-small-en-v1.5', 'index_seconds': 42.1}
- Options: {'retrieval_mode': 'dense', 'top_k': 4, 'citation_policy': 'strict', 'generation': 'structured'}
- Generator: capstone  |  judge: capstone (temperature 0)

## Retrieval (no LLM)

| Slice | n | R@1 | R@5 | R@10 | MRR | groupR@1 | groupR@5 | groupR@10 |
|---|---|---|---|---|---|---|---|---|
| **overall** | 15 | 0.867 | 0.933 | 0.933 | 1.000 | 0.933 | 0.967 | 0.967 |
| split: dev | 15 | 0.867 | 0.933 | 0.933 | 1.000 | 0.933 | 0.967 | 0.967 |
| factual | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| keyword | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| multi_section | 2 | 0.000 | 0.500 | 0.500 | 1.000 | 0.500 | 0.750 | 0.750 |
| near_miss | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| paraphrase | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| procedural | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| reasoning | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Answers (LLM-judged)

| Metric | Value |
|---|---|
| Key-fact coverage (answerable, n=15) | 0.961 |
| All key facts correct | 0.867 |
| Faithfulness (sentences supported by excerpts) | 0.938 |
| Citation precision (citing sentences supported by their cited excerpts) | 0.970 |
| Citation coverage (sentences that cite something) | 1.000 |
| Answers citing a gold-evidence chunk | 1.000 |
| Abstention accuracy (unanswerable, n=3) | 0.667 |
| False abstention (answerable but declined) | 0.000 |
| Refusals that still cited excerpts (prompt violation) | 0 |
| Avg context given to the LLM (chars) | 3828.167 |
| Citation check: invalid citations removed / uncited sentences dropped / refused | 0 / 23 / 0 |
| Errors | 0 of 18 |

### Retrieval vs generator

| Generator's context had all gold evidence? | n | Key-fact coverage | All facts correct |
|---|---|---|---|
| yes | 14 | 0.976 | 0.929 |
| no | 1 | 0.750 | 0.000 |

### Correctness by question type

| Type | n | Key-fact coverage | All facts correct |
|---|---|---|---|
| factual | 3 | 1.000 | 1.000 |
| keyword | 2 | 1.000 | 1.000 |
| multi_section | 2 | 0.708 | 0.000 |
| near_miss | 2 | 1.000 | 1.000 |
| paraphrase | 2 | 1.000 | 1.000 |
| procedural | 2 | 1.000 | 1.000 |
| reasoning | 2 | 1.000 | 1.000 |

## Diagnosis

- Generator context contained all gold evidence for 0.933 of answerable questions.
- When the evidence WAS retrieved, key-fact coverage was 0.976 (n=14) -> this measures the generator.
- When it was NOT retrieved, coverage was 0.750 (n=1) -> these failures are retrieval's fault.
- Verdict: both stages are performing well on this set.
