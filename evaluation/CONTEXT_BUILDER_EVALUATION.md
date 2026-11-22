# FinTell Context Builder — Golden Dataset evaluation

This is an **isolated agent evaluation**, separate from `evaluation/run.py` (which tests the entire LangGraph chatbot). It calls the project's real `src/agents/context_builder.py` function and uses the exact `prompts/context_builder_prompt.yml` and `ContextBuilderResponse.contextual_queries` schema.

## Data and status

- Dataset: `evaluation/context_builder_golden_100_audited.jsonl` — 100 banking-focused **draft** ground-truth examples. An Excel review copy is provided separately and should remain the human review source.
- 98 cases are eligible for model comparison; **CB086 and CB098 are excluded** because their date interpretations need a team decision.
- All 100 labels still require a human owner to approve them before they can be called authoritative *golden truth*.
- Fixed interpretation date: **2026-11-22**. The runner temporarily patches `datetime` **only inside the Context Builder module**, while invoking that agent, to avoid changing production code or interpreting "last month" differently next year.
- `chat_history_messages` is a list of `{ "role": "user" | "assistant", "content": "..." }`, oldest first. The latest message is passed separately via `UIUserRequest.user_query`.

## How to run

Run from the **repository root**. The first two commands use only the Python standard library and make **no model calls**:

```bash
python -m evaluation.context_builder_run --validate-only
python -m unittest discover -s tests -p 'test_context_builder_evaluation.py' -v
```

For an **approved small live pilot** (requires the repository's existing Python dependencies, a working Qwen provider, and securely configured `QWEN_API_KEY` / `QWEN_CA_CERT_PATH`):

```bash
python -m pip install -r requirements.txt
# Set LLM_PROVIDER=qwen and secrets through your approved environment mechanism.
python -m evaluation.context_builder_run --live --limit 3 \
  --as-of 2026-11-22 \
  --output evaluation/results/context_builder_report.json
```

To select a specific safe test case, use `--ids CB001 CB002 --limit 2`. To evaluate more approved cases, increase `--limit` (maximum is only constrained by the dataset). By default the runner **skips** CB086 and CB098; include them only after the prompt/ground-truth rules have been resolved, using `--include-flagged`. Live requests run sequentially with a 2-second pause because the group key allows only one active request.

## GitHub Actions (no local software required)

The `.github/workflows/context-builder-evaluation.yml` workflow runs free offline validation automatically on matching changes to `feature/evaluation-framework`. A normal push **does not call Qwen**.

To opt into a *three-case* live pilot on this feature branch, first confirm shared-key usage is approved and no other evaluations are running. Then make a harmless comment-only edit to `.github/workflows/context-builder-evaluation.yml` and commit with the **exact marker** `[run-context-pilot]` in the commit message, e.g. `Run Context Builder pilot [run-context-pilot]`. The workflow's live job will run **only three cases**. For larger runs, once the workflow exists on the repository's default branch, use **Actions → FinTell Context Builder Golden Evaluation → Run workflow**, select the evaluation branch, opt into live calls, and choose the number of cases. Check artifacts for `fintell-context-builder-report`.

Repository Actions secrets must contain **`QWEN_API_KEY`** and **`QWEN_CA_CERT_B64`**, with the CA certificate belonging to the API server. Do not commit the certificate, Base64 value, API key, `.env`, or private response logs. GitHub workflow runs may consume API quota.

## Interpreting results

The JSON report contains each case's expected and actual `contextual_queries`, latency, reported tokens and diagnostics:

| Verdict | Meaning |
|---|---|
| `exact_match` | Differences only in casing, whitespace, or terminal punctuation; a baseline match to **draft** ground truth. |
| `needs_human_review` | Same number of queries but non-identical wording. This **may be a correct paraphrase**. |
| `potential_mismatch` | Query count differs or a date anchor is missing/changed; a **review signal**, not a conclusive semantic error. |
| `invalid_output` | No valid list of contextual queries returned. |
| `agent_error` | The actual Context Builder returned a fallback or raised an exception. The report includes **type only**, never raw exception text. |

**Do not report the exact text match rate as semantic accuracy.** A reviewer must adjudicate paraphrases, entity/timeframe preservation, question splitting, and the reference labels themselves. The score is based on synthetic cases and draft ground truth. `total_tokens` is the app's reported usage; its existing cost estimator uses Anthropic pricing and is **not** used to estimate Qwen billing here.

## Important limitations

- The standalone runner does **not** evaluate supervisor routing, SQL execution, guardrails or HelpCenter answers; those belong to the existing end-to-end suite.
- The dataset still includes many synthetic assistant replies. Realistic multi-turn histories and team-approved correct answers are improvements for later iterations.
- This package includes no private API key. Offline tests use a fake model while importing and invoking the **real** Context Builder function to verify argument shape and evaluation date.
- Live structured-output compatibility with your Qwen gateway **has not been tested by this package**; any failure will be captured in the report. The earlier simple LangChain connection success is not sufficient to prove structured output works.
- Reports may contain user messages and model outputs. They are stored under the repository's gitignored `evaluation/results/` folder; review artifact access and retention before running with anything other than approved synthetic test data.

## Integration without disturbing the existing pull request

Add the five new paths below to your current `feature/evaluation-framework` branch; do not overwrite any existing chatbot files:

- `evaluation/context_builder_golden_100_audited.jsonl`
- `evaluation/context_builder_run.py`
- `evaluation/CONTEXT_BUILDER_EVALUATION.md`
- `tests/test_context_builder_evaluation.py`
- `.github/workflows/context-builder-evaluation.yml`

Commit with `Add isolated Context Builder golden dataset evaluation`. The offline workflow will validate on push. Existing PR #2 will update automatically when you push to the same branch, but **reviewers still need to approve the draft labels and rule decisions before calling it complete**.
