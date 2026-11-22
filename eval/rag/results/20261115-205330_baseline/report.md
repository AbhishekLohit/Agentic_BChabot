# RAG evaluation: baseline

- Run: 2026-11-15T20:53:30  |  eval set: v1  |  split: dev
- Index: {'files': 1, 'chunks': 16, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'local', 'index_seconds': 1.7}

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

## Diagnosis

- Retrieval recall@5 = 0.967. Run the 'answers' stage to see whether the generator uses the evidence it gets.
