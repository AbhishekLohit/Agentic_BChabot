# Context Builder evaluation

This isolated evaluation invokes the real `src/agents/context_builder.py` function with the project's prompt and `ContextBuilderResponse.contextual_queries` schema. It does not run the full graph and does not replace the RAG retrieval evaluation under `evaluation/rag/`.

## Dataset and status

- `evaluation/evaluation_cases.jsonl` is the shared suite-tagged golden dataset; 100 records use `suite: "context_builder"`.
- 98 cases are eligible for comparison. CB086 and CB098 are skipped by default because their date interpretations need a team decision.
- All reference labels require human approval; exact text matches are not semantic accuracy.
- The fixed interpretation date is 2026-11-22. The runner patches `datetime` only inside the Context Builder module during evaluation.

## Offline validation

Run from the repository root. Validation checks the dataset without making model calls; the tests use a fake model while invoking the real Context Builder agent.

```powershell
python -m evaluation.context_builder_run --validate-only
python -m unittest discover -s tests -p 'test_context_builder_evaluation.py' -v
```

Install the project's dependencies first if needed:

```powershell
python -m pip install -r requirements.txt
```

## Live evaluation

Configure the selected provider in the repository-root `.env`. For Qwen, follow the `LLM_PROVIDER=qwen` setup in the root README, including the assigned API key and CA certificate. The runner loads `.env` before checking `LLM_PROVIDER`.

Start with a small, approved pilot:

```powershell
python -m evaluation.context_builder_run --live --limit 3 --as-of 2026-11-22
```

To select particular cases, use `--ids CB001 CB002 --limit 2`. The full eligible set can be run with `--limit 98` after confirming API usage is approved. Include CB086 or CB098 only after resolving their interpretation rules, using `--include-flagged`.

Reports are written under `evaluation/results/`, which is gitignored. They may contain prompts and model outputs; review access and retention before sharing.

## Results and limitations

The report includes individual verdicts and a failure taxonomy:

- `agent_error`: the agent returned a fallback or raised an exception.
- `invalid_output`: no valid contextual-query list was returned.
- `query_count_mismatch`: generated and reference query counts differ.
- `date_anchor_mismatch`: a concrete date anchor differs or is missing.
- `potential_mismatch_needs_review`: other likely mismatches against the draft reference.
- `non_exact_output_needs_human_review`: non-exact wording with matching query count/date anchors.

These are review signals against draft labels, not confirmed semantic failures. A reviewer must assess paraphrases, entity and timeframe preservation, question splitting, and the reference labels. The runner does not evaluate supervisor routing, SQL execution, guardrails, or HelpCenter answers. Live structured-output compatibility must be confirmed for the selected provider; offline tests do not establish it.
