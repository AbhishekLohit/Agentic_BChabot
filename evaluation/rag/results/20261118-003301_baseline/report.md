# RAG evaluation: baseline

- Run: 2026-11-18T00:33:01  |  eval set: unfrozen  |  split: dev
- Index: {'files': 687, 'chunks': 800, 'chunk_strategy': 'fixed', 'avg_chunk_chars': 825, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'capstone', 'embedding_model': 'capstone:bge-small-en-v1.5', 'index_seconds': 26.2}
- Options: {'retrieval_mode': 'dense', 'top_k': 4, 'citation_policy': 'strict', 'generation': 'structured'}

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

## Diagnosis

- Retrieval recall@5 = 0.933. Run the 'answers' stage to see whether the generator uses the evidence it gets.
