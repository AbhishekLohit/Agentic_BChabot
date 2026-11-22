# Chunking experiments (dev split)

- Run: 2026-11-15 17:36  |  eval set: v1  |  60 answerable questions
- Embeddings: local  |  corpus: 1 file(s)

## Results

| Strategy | Chunks | Avg chunk (chars) | R@1 | R@5 | R@10 | MRR | Chars the LLM reads (top 4) | Gold quotes split |
|---|---|---|---|---|---|---|---|---|
| fixed-500 | 39 | 457 | 0.650 | 0.933 | 1.000 | 0.800 | 1884 | 0.0% |
| fixed-1000 | 21 | 835 | 0.633 | 0.967 | 1.000 | 0.820 | 3546 | 0.0% |
| fixed-1200 (current) | 16 | 1077 | 0.783 | 0.967 | 1.000 | 0.884 | 4438 | 0.0% |
| fixed-1500 | 15 | 1149 | 0.633 | 0.917 | 0.983 | 0.765 | 4752 | 0.0% |
| structure (1 per Q&A) | 45 | 311 | 0.733 | 1.000 | 1.000 | 0.885 | 1338 | 0.0% |
| structure + section header **(best MRR)** | 45 | 353 | 0.800 | 1.000 | 1.000 | 0.926 | 1472 | 0.0% |

## Recall@1 by question type

| Strategy | factual (n=10) | keyword (n=6) | multi_section (n=5) | near_miss (n=5) | paraphrase (n=16) | procedural (n=10) | reasoning (n=8) |
|---|---|---|---|---|---|---|---|
| fixed-500 | 0.80 | 0.67 | 0.00 | 0.80 | 0.75 | 0.60 | 0.62 |
| fixed-1000 | 0.80 | 0.50 | 0.20 | 0.40 | 0.69 | 0.80 | 0.62 |
| fixed-1200 (current) | 0.90 | 0.83 | 0.40 | 0.80 | 0.75 | 0.70 | 1.00 |
| fixed-1500 | 0.60 | 0.83 | 0.40 | 0.40 | 0.62 | 0.60 | 0.88 |
| structure (1 per Q&A) | 1.00 | 0.67 | 0.00 | 0.60 | 0.81 | 0.80 | 0.75 |
| structure + section header | 1.00 | 0.83 | 0.00 | 0.60 | 0.88 | 0.90 | 0.88 |

## Questions where the strategies disagree most

Rank of the first chunk containing gold evidence (1 = top; - = not in top 10).

| Question | fixed-500 | fixed-1000 | fixed-1200 (current) | fixed-1500 | structure (1 per Q&A) | structure + section header |
|---|---|---|---|---|---|---|
| E045 | 2 | 1 | 1 | - | 1 | 1 |
| E012 | 3 | 8 | 10 | 8 | 2 | 1 |
| E078 | 8 | 2 | 2 | 2 | 1 | 2 |
| E009 | 7 | 1 | 3 | 3 | 2 | 1 |
| E025 | 7 | 1 | 1 | 1 | 1 | 1 |
| E034 | 1 | 5 | 1 | 7 | 1 | 1 |
| E022 | 6 | 1 | 1 | 1 | 1 | 1 |
| E029 | 1 | 5 | 6 | 6 | 1 | 1 |
| E043 | 1 | 3 | 1 | 6 | 1 | 1 |
| E001 | 1 | 1 | 1 | 1 | 5 | 1 |

## How to read this

- With 60 questions, one question moves recall by 0.017; treat differences smaller than about 0.03 as noise.
- **MRR and R@1 matter most** for the app: the LLM only sees the top 4 chunks, and higher-ranked evidence is used more reliably by a small model.
- **Chars the LLM reads** is the cost side: smaller chunks put less text in the 8K-token context, leaving room for more chunks or a longer answer.
- **Gold quotes split** caps recall: a quote cut across two chunks can never be retrieved whole.
- Pick on the dev split, then confirm the winner once on the test split. Re-run after the corpus grows.
