# Retrieval modes - dev split (18 questions, eval set unfrozen)

Index: {'files': 687, 'chunks': 800, 'chunk_strategy': 'fixed', 'avg_chunk_chars': 825, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'capstone', 'embedding_model': 'capstone:bge-small-en-v1.5', 'index_seconds': 30.2}

| Mode | R@1 | R@5 | R@10 | MRR | Group R@5 | ms/query |
|---|---|---|---|---|---|---|
| dense | 0.867 | 0.933 | 0.933 | 1.000 | 0.967 | 93.3 |
| bm25 | 0.467 | 0.800 | 0.867 | 0.637 | 0.800 | 23.2 |
| hybrid | 0.667 | 0.800 | 0.933 | 0.799 | 0.833 | 92.0 |
| hybrid_rerank | 0.867 | 0.933 | 0.933 | 0.967 | 0.967 | 5397.1 |

## Recall@5 by question type

| Type | dense | bm25 | hybrid | hybrid_rerank |
|---|---|---|---|---|
| factual | 1.000 | 0.667 | 0.667 | 1.000 |
| keyword | 1.000 | 1.000 | 1.000 | 1.000 |
| multi_section | 0.500 | 0.500 | 0.500 | 0.500 |
| near_miss | 1.000 | 0.500 | 0.500 | 1.000 |
| paraphrase | 1.000 | 1.000 | 1.000 | 1.000 |
| procedural | 1.000 | 1.000 | 1.000 | 1.000 |
| reasoning | 1.000 | 1.000 | 1.000 | 1.000 |

Recall@k = every evidence group of the question is inside the top k chunks. MRR = 1 / rank of the first chunk holding any evidence.
