# Chunking experiments (dev split)

- Run: 2026-11-18 00:40  |  eval set: unfrozen  |  15 answerable questions
- Embeddings: capstone  |  corpus: 687 file(s)

## Results

| Strategy | Chunks | Avg chunk (chars) | R@1 | R@5 | R@10 | MRR | Chars the LLM reads (top 4) | Gold quotes split |
|---|---|---|---|---|---|---|---|---|
| fixed-500 | 1743 | 411 | 0.467 | 0.867 | 0.933 | 0.678 | 1795 | 0.0% |
| fixed-1000 | 959 | 708 | 0.867 | 0.933 | 0.933 | 0.967 | 3232 | 0.0% |
| fixed-1200 (current) **(best MRR)** | 800 | 825 | 0.867 | 0.933 | 0.933 | 1.000 | 3905 | 0.0% |
| fixed-1500 **(best MRR)** | 709 | 906 | 0.867 | 0.933 | 0.933 | 1.000 | 4064 | 0.0% |
| structure (1 per Q&A) **(best MRR)** | 709 | 903 | 0.867 | 0.933 | 0.933 | 1.000 | 4012 | 0.0% |
| structure + section header | 709 | 942 | 0.867 | 0.933 | 0.933 | 0.967 | 4145 | 0.0% |

## Recall@1 by question type

| Strategy | factual (n=3) | keyword (n=2) | multi_section (n=2) | near_miss (n=2) | paraphrase (n=2) | procedural (n=2) | reasoning (n=2) |
|---|---|---|---|---|---|---|---|
| fixed-500 | 0.67 | 0.50 | 0.00 | 0.00 | 1.00 | 0.00 | 1.00 |
| fixed-1000 | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| fixed-1200 (current) | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| fixed-1500 | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| structure (1 per Q&A) | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| structure + section header | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 |

## Questions where the strategies disagree most

Rank of the first chunk containing gold evidence (1 = top; - = not in top 10).

| Question | fixed-500 | fixed-1000 | fixed-1200 (current) | fixed-1500 | structure (1 per Q&A) | structure + section header |
|---|---|---|---|---|---|---|
| E007 | 9 | 1 | 1 | 1 | 1 | 1 |
| E018 | 7 | 2 | 1 | 1 | 1 | 2 |
| E006 | 4 | 1 | 1 | 1 | 1 | 1 |
| E009 | 3 | 1 | 1 | 1 | 1 | 1 |
| E015 | 3 | 1 | 1 | 1 | 1 | 1 |
| E010 | 2 | 1 | 1 | 1 | 1 | 1 |
| E014 | 2 | 1 | 1 | 1 | 1 | 1 |
| E001 | 1 | 1 | 1 | 1 | 1 | 1 |
| E002 | 1 | 1 | 1 | 1 | 1 | 1 |
| E003 | 1 | 1 | 1 | 1 | 1 | 1 |

## How to read this

- With 15 questions, one question moves recall by 0.067; treat differences smaller than about 0.13 as noise.
- **MRR and R@1 matter most** for the app: the LLM only sees the top 4 chunks, and higher-ranked evidence is used more reliably by a small model.
- **Chars the LLM reads** is the cost side: smaller chunks put less text in the 8K-token context, leaving room for more chunks or a longer answer.
- **Gold quotes split** caps recall: a quote cut across two chunks can never be retrieved whole.
- Pick on the dev split, then confirm the winner once on the test split. Re-run after the corpus grows.
