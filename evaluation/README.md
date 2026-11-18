# FinTell evaluation framework

Evaluates the **existing LangGraph workflow** (guardrail -> context builder -> supervisor -> BankSQL and/or Barclays help RAG -> response generation). No production endpoints or agent prompts are modified.

## What is measured

| Criterion | Method | Limitations |
|---|---|---|
| Routing | Exact comparison of activated BankSQL, HelpCenter, Out_of_scope or guardrail branches | Mixed-intent tests can specify multiple routes |
| SQL result accuracy | Compare SQLite execution output against independent, curated reference SELECT (aliases ignored) | Strong for scalar queries; order matters unless `unordered=true`; complex data equivalence needs additional fixtures |
| SQL read-only signal | Generated query begins `SELECT` or `WITH` | Not a comprehensive SQL security verifier |
| Fallback/safety | Expected fallback flag and route, optional excluded answer phrases | Does not replace adversarial security review |
| Response content | Case-insensitive required / forbidden substrings | **Not** a semantic correctness or hallucination metric; add human/LLM review for open-ended answers |
| Cost and latency | Sum reported per-agent token/cost metrics and wall-clock seconds | Model pricing estimates depend on existing app metrics |

Each case passes only when every configured check passes. The JSON report contains aggregate pass rate, per-criterion rates, latency, token and cost estimates, and a case-level PASS/FAIL without raw customer responses.

## Run

From project root, install existing project dependencies (`pip install -r requirements.txt`).

```bash
# Free, offline: validate all cases and independent SQLite reference queries
python -m evaluation.run --validate-only

# Free, offline: test the scoring engine
python -m unittest discover -s tests -p 'test_evaluation.py' -v

# Live: calls the configured model provider and incurs API usage charges
# Configure .env following the app's normal instructions first.
python -m evaluation.run --output evaluation/results/run.json

# Include help-centre retrieval (needs the existing Chroma store and embedding credentials)
python -m evaluation.run --enable-help
```

`--dataset` selects another JSONL file; `--db` selects another SQLite transaction database. Help-centre tests will generally fail without `--enable-help`, by design. The default dataset uses `Cust_0410`, which exists in the bundled demonstration database; adjust IDs and references if running against a different database. For strict reproducibility, freeze the model/version, DB snapshot, prompts and dataset revision.

## Dataset schema

One JSON object per line in `evaluation/cases.jsonl`. Required: `id`, `category`, `question`, `expected_routes`. Optional: `customer_id`, `history` (array of `{"role":"user|assistant", "content":"..."}`), `expected_sql`, `unordered`, `expected_fallback`, `answer_contains`, `answer_excludes`. `expected_sql` is a trusted reference SELECT evaluated on the original read-only database; **never import untrusted reference SQL files**. Scoring is deterministic; the live model output remains stochastic.

## Safety and limitations

- The live runner creates a **temporary copy of the SQLite database** for the chatbot; it reads the original database in read-only mode for independent expectations. It does not call the FastAPI endpoint, persist chat history to Redis, or alter production code.
- It *does* send test prompts to the configured LLM provider, and may send retrieved help-centre excerpts. Use synthetic/approved examples only.
- SQL generation remains the app's current implementation; `sql_read_only` is diagnostic. A separate application-level read-only SQL enforcement change is recommended before production use.
- No API key is required for validation or unit tests. Live evaluation **was not validated against a model provider** unless a live report is explicitly available.
- The existing `tests/data/*.xlsx` fixtures include restaurant-order scenarios, so they were **not** assumed to be banking ground truth. They remain untouched.
- Do not commit generated reports: they are gitignored. Review failures and add human-reviewed reference cases before adopting a CI release gate; suggested initial gate is 100% on security cases and >= 90% on other critical curated cases after baseline validation.
