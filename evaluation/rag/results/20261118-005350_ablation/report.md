# Small-model ablation - dev split, eval set unfrozen

Generator: anthropic; judge: anthropic (temperature 0); index: {'files': 687, 'chunks': 800, 'chunk_strategy': 'fixed', 'avg_chunk_chars': 825, 'chunk_size': 1200, 'chunk_overlap': 200, 'embedding_provider': 'capstone', 'embedding_model': 'capstone:bge-small-en-v1.5', 'index_seconds': 40.8}

| Metric | Baseline (dense top-8, plain text, no citation check) | + structured output | + shorter context (top-4) | + hybrid search (BM25 + dense, RRF) | + cross-encoder reranker | + strict citations / abstaining |
|---|---|---|---|---|---|---|
| Context recall | 0.933 | 0.933 | 0.933 | 0.800 | 0.933 | 0.933 |
| Key-fact coverage | 0.928 | 0.944 | 0.911 | 0.828 | 0.911 | 0.928 |
| All facts correct | 0.733 | 0.800 | 0.667 | 0.667 | 0.667 | 0.733 |
| Faithfulness | 0.938 | 0.988 | 0.938 | 1.000 | 0.988 | 1.000 |
| Citation precision | 0.979 | 1.000 | 0.900 | 1.000 | 0.969 | 1.000 |
| Abstains on unanswerable | 0.667 | 0.667 | 0.667 | 0.667 | 0.667 | 0.667 |
| False abstention | 0.000 | 0.000 | 0.000 | 0.067 | 0.000 | 0.000 |
| Context chars | 7493 | 7493 | 3828 | 3701 | 3829 | 3829 |
| Seconds / answer | 2.1 | 2.1 | 1.9 | 6407.2 | 2.1 | 2.0 |
| Errors | 0 | 0 | 0 | 0 | 0 | 0 |

## Effect of each change (vs the rung before it)

- **+ structured output**: Key-fact coverage 0.93 -> 0.94 (better); All facts correct 0.73 -> 0.80 (better); Faithfulness 0.94 -> 0.99 (better); Citation precision 0.98 -> 1.00 (better)
- **+ shorter context (top-4)**: Key-fact coverage 0.94 -> 0.91 (worse); All facts correct 0.80 -> 0.67 (worse); Faithfulness 0.99 -> 0.94 (worse); Citation precision 1.00 -> 0.90 (worse)
- **+ hybrid search (BM25 + dense, RRF)**: Context recall 0.93 -> 0.80 (worse); Key-fact coverage 0.91 -> 0.83 (worse); Faithfulness 0.94 -> 1.00 (better); Citation precision 0.90 -> 1.00 (better); False abstention 0.00 -> 0.07 (worse)
- **+ cross-encoder reranker**: Context recall 0.80 -> 0.93 (better); Key-fact coverage 0.83 -> 0.91 (better); Faithfulness 1.00 -> 0.99 (worse); Citation precision 1.00 -> 0.97 (worse); False abstention 0.07 -> 0.00 (better)
- **+ strict citations / abstaining**: Key-fact coverage 0.91 -> 0.93 (better); All facts correct 0.67 -> 0.73 (better); Faithfulness 0.99 -> 1.00 (better); Citation precision 0.97 -> 1.00 (better)
