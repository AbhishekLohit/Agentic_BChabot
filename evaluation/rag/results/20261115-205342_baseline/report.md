# RAG evaluation: baseline

- Run: 2026-11-15T20:53:42  |  eval set: v1  |  split: dev
- Index: {'files': 1, 'chunks': 16, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'local', 'index_seconds': 1.4}
- Generator: capstone  |  judge: capstone (temperature 0)

## Retrieval (no LLM)

| Slice | n | R@1 | R@5 | R@10 | MRR | groupR@1 | groupR@5 | groupR@10 |
|---|---|---|---|---|---|---|---|---|
| **overall** | 60 | 0.783 | 0.967 | 1.000 | 0.884 | 0.800 | 0.967 | 1.000 |
| split: dev | 60 | 0.783 | 0.967 | 1.000 | 0.884 | 0.800 | 0.967 | 1.000 |
| factual | 10 | 0.900 | 0.900 | 1.000 | 0.917 | 0.900 | 0.900 | 1.000 |
| keyword | 6 | 0.833 | 0.833 | 1.000 | 0.850 | 0.833 | 0.833 | 1.000 |
| multi_section | 5 | 0.400 | 1.000 | 1.000 | 0.867 | 0.600 | 1.000 | 1.000 |
| near_miss | 5 | 0.800 | 1.000 | 1.000 | 0.900 | 0.800 | 1.000 | 1.000 |
| paraphrase | 16 | 0.750 | 1.000 | 1.000 | 0.865 | 0.750 | 1.000 | 1.000 |
| procedural | 10 | 0.700 | 1.000 | 1.000 | 0.808 | 0.700 | 1.000 | 1.000 |
| reasoning | 8 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Answers (LLM-judged)

| Metric | Value |
|---|---|
| Key-fact coverage (answerable, n=60) | 0.958 |
| All key facts correct | 0.950 |
| Faithfulness (sentences supported by excerpts) | 0.983 |
| Citation precision (citing sentences supported by their cited excerpts) | 0.988 |
| Citation coverage (sentences that cite something) | 0.795 |
| Answers citing a gold-evidence chunk | 0.948 |
| Abstention accuracy (unanswerable, n=10) | 0.900 |
| False abstention (answerable but declined) | 0.033 |
| Refusals that still cited excerpts (prompt violation) | 3 |
| Errors | 0 of 70 |

### Retrieval vs generator

| Generator's context had all gold evidence? | n | Key-fact coverage | All facts correct |
|---|---|---|---|
| yes | 58 | 0.974 | 0.966 |
| no | 2 | 0.500 | 0.500 |

### Correctness by question type

| Type | n | Key-fact coverage | All facts correct |
|---|---|---|---|
| factual | 10 | 0.900 | 0.900 |
| keyword | 6 | 1.000 | 1.000 |
| multi_section | 5 | 1.000 | 1.000 |
| near_miss | 5 | 0.800 | 0.800 |
| paraphrase | 16 | 1.000 | 1.000 |
| procedural | 10 | 1.000 | 1.000 |
| reasoning | 8 | 0.938 | 0.875 |

## Diagnosis

- Generator context contained all gold evidence for 0.967 of answerable questions.
- When the evidence WAS retrieved, key-fact coverage was 0.974 (n=58) -> this measures the generator.
- When it was NOT retrieved, coverage was 0.500 (n=2) -> these failures are retrieval's fault.
- Verdict: both stages are performing well on this set.
