# RAG evaluation: v2_starter

- Run: 2026-11-17T17:58:55  |  eval set: unfrozen  |  split: all
- Index: {'files': 687, 'chunks': 800, 'chunk_strategy': 'fixed', 'avg_chunk_chars': 825, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'local', 'index_seconds': 98.7}

## Retrieval (no LLM)

| Slice | n | R@1 | R@5 | R@10 | MRR | groupR@1 | groupR@5 | groupR@10 |
|---|---|---|---|---|---|---|---|---|
| **overall** | 18 | 0.833 | 0.944 | 1.000 | 0.963 | 0.889 | 0.972 | 1.000 |
| split: dev | 15 | 0.800 | 0.933 | 1.000 | 0.956 | 0.867 | 0.967 | 1.000 |
| split: test | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| factual | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| keyword | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| multi_section | 2 | 0.000 | 0.500 | 1.000 | 1.000 | 0.500 | 0.750 | 1.000 |
| near_miss | 2 | 0.500 | 1.000 | 1.000 | 0.667 | 0.500 | 1.000 | 1.000 |
| paraphrase | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| procedural | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| reasoning | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

## Diagnosis

- Retrieval recall@5 = 0.944. Run the 'answers' stage to see whether the generator uses the evidence it gets.
