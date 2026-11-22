## Compensating for a small model (Qwen3-8B)

All numbers: dev split of the frozen eval set (unfrozen), generator anthropic, judged by anthropic (temperature 0). Each fix was added on top of the previous one, so each row isolates one change.

| Fix | What changed | Before | After |
|---|---|---|---|
| Structured output | JSON schema instead of free text | faithfulness 0.938 | faithfulness 0.988 |
| Shorter context for the 8K window | top-8 -> top-4 chunks | key-fact coverage 0.944 | key-fact coverage 0.911 |
| Hybrid search | BM25 + dense, reciprocal rank fusion | context recall 0.933 | context recall 0.800 |
| Reranker | cross-encoder over the top-30 hybrid hits | context recall 0.800 | context recall 0.933 |
| Strict citations / abstaining | invalid cites removed, uncited sentences dropped, refuse without support | citation precision 0.969 | citation precision 1.000 |

Notes to write from the table above:
- Context size: 7493 -> 3828 characters per prompt.
- Abstention on unanswerable questions: 0.667 -> 0.667; false abstention 0.000 -> 0.000 (the cost of being strict).
- Diagnosis: compare context recall with key-fact coverage. High recall but low coverage means the 8B generator is the bottleneck; low recall means retrieval is.
