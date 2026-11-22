---
title: FinTell — Transaction Charts · Addendum
companion: prd.md
created: 2026-11-22
updated: 2026-11-22
---

# Addendum — notes for architecture

Notes raised while writing and reviewing the PRD. They are inputs for `bmad-architecture`, not PRD requirements. The detailed spine reconciliation is in `reconcile-architecture-spine.md` (G-1 to G-14).

## Spine items this feature triggers

The baseline spine defers these decisions to "the next feature that adds a node, output type or branch". This is that feature.

| Spine deferred item | Why it matters here | PRD link |
| --- | --- | --- |
| How the envelope grows for new outputs | A Chart is the first output type beyond text. The existing `visualization` envelope key is always `null` today. | FR-11 |
| Whether new outputs are persisted with `chat_messages` | Decided for v1: Charts are not persisted; the FR-5 note is, as part of the text. The Chart Decision appears only in the trace. | FR-5, FR-12, FR-14 |
| Who may write `fallback_response` | A Chart Path failure must not divert or prefix the Reply. | FR-13 |
| How `agent_metrics` merges (no reducer) | A new stage writing metrics must not overwrite other stages' metrics. | FR-14 |
| Who owns the branch order | A new stage must chain sequentially and converge at `join` (AD-5). | — |
| A central node registry vs manual AD-12 registration | One more node is still within "manual is workable", but registration in `_STREAM_STEP_LABELS`, `_build_agent_trace` and the `index.html` icon map is easy to miss. Reuse the `visualization_agent` id or add a new one, and use that one id everywhere (AD-12 forbids new aliases). | FR-14 |
| Fate of the unused visualization code | Reuse or remove. The PRD requires only that it does not conflict. | §6.1 |

## Unused visualization code (from the spine)

Not a design to follow; listed so architecture can decide each item.

- `VisualizationDecision`, `VisualizationResponse` in `src/app_models.py`
- `Graph_State.visualization_response`
- the `visualization` envelope key in both endpoints
- the `visualization_agent` entries in `_STREAM_STEP_LABELS` and `_build_agent_trace`
- the Plotly renderer and icon in `src/index.html` (Plotly.js 2.35.2 from a CDN)
- the unimported Python `plotly` package in `requirements.txt`

## Where the chart step sits (G-2)

Three requirements together fix the step's position:

- **FR-5:** the note needs the Chart Decision before `response_generator` writes the text. `response_generator` is the only text writer (AD-3), so the note reaches it as a state field, not through a second writer.
- **FR-10:** time to first token is unchanged (within 200 ms). Anything before `response_generator` must therefore add no meaningful latency: no LLM call on that path.
- **FR-14:** every Reply carries a Chart Decision, including on the fallback and help-only paths. The decision therefore runs on every path, after `join`.

This points to a **code-only decision node at `join → chart_decision → response_generator`**. Building the Chart itself (titles, the spec sent to the browser) can be code in the same node, or split off to run after `response_generator` if needed. Parallel execution stays banned (AD-5).

## Decision mechanism (open for architecture)

The PRD requires the Chart Eligibility Rules to be deterministic in outcome, and FR-10 effectively requires the decision to be code-only. Shape detection (Category vs Time Point vs Raw Transaction List) should be plain code over the Result's columns and values. The counting limits (3–8, 3–12) and the Skip Reason precedence are enforced in code, so SM-1 can reach 100%.

## Optional-stage exception to AD-6 and AD-7 (G-3)

AD-6 says a failing stage sets `fallback_response`; FR-13 forbids that for the Chart Path. Architecture should amend AD-6 and AD-7 with an "optional stage" class. Such a stage:

- catches everything;
- never touches `fallback_response`;
- records its failure on its own output field (Skip Reason `chart_error`) and in metrics.

The `fallback` Skip Reason checks whether `state.fallback_response` is set, not the message text. That covers `needs_separate_questions` (which has no message), a leftover `query_error`, and a `help_center` failure (G-7).

## FR-15: prompt change, customer scoping, row cap and cache (G-4, G-6, G-8)

- **Prompt change.** FR-15 needs a `prompts/text2sql_generator.yml` change so that balance-trend questions select the last row's `balance` for each period instead of returning one row per transaction.
- **Customer scoping (AD-11).** Isolation is enforced only by the prompt. Every subquery or CTE in the new guidance and examples must apply `customer_id = '<customer_id>'`. The eval set includes the same balance-trend question for two customers. Any chart-friendly alias convention added to the prompt (for example `category` / `period` / `amount`) is also an AD-11 change.
- **Row cap (AD-10).** The aggregate check is a regex over `count|sum|avg|min|max|group_concat(`. A window-function query with no aggregate is treated as a detail query: it gets `LIMIT 20`, plus `ORDER BY transaction_date DESC` if it has none. The FR-15 SQL should therefore contain an aggregate (for example `MAX(transaction_date)`), or AD-10's detection must be extended. A Result that carries the AD-10 notice string is a Raw Transaction List and is never charted.
- **Cache.** After the prompt change, run `DELETE FROM query_cache` and delete the Redis `qcache:*` keys. The cache key is the supervisor's wording (`str(banksql_queries)`), so a supervisor prompt change also changes which SQL gets reused. SQL that failed and was then corrected stays cached (AD-2 as-is).

## Explicit Chart Request detection (G-5)

Architecture decides which stage produces the Explicit Chart Request flag. Deterministic code over `user_query` is preferred because it adds no LLM latency. The guardrail (whose prompt is hard-coded) and the supervisor prompt must treat chart wording as in scope. The eval set includes each UJ-4 phrasing to prove those questions are not sent to fallback. A new structured field on the supervisor output is a change to `app_models.py`.

## Streaming (G-9)

- Chart Path LLM calls, if any, run only in the chart node, which is not on the token allowlist.
- The Chart travels only in the `final` event. No new SSE event type is added.
- UX decides whether the step label ("Preparing a chart") shows when the decision is "no chart".

## Other notes

- **No new endpoint is expected (G-12).** If one is added (for example to fetch the Chart after the stream), it follows AD-16 (`20/minute`) and AD-13. Chart LLM cost figures are only indicative, because Haiku rates are hard-coded (AD-16 as-is).
- **No side-channel SQL (G-13).** The Chart Path reads only `database_query_results`. Any reshaping is done in code. Any new SQL goes through `sql_execution` (AD-11).
- **Plotly CDN (G-11).** For an offline demo, consider serving Plotly locally. FR-13 requires the page to degrade gracefully either way.
- **Parity (G-14).** SM-4 excludes the paths where the endpoints already differ. Folding in the deferred shared post-graph helper is a natural fit but optional.
- **Result rows** are `list[str]` of `str(dict)`. Whether the Chart Path parses these or reads a structured form is a design choice. Either way, ignore the trailing AD-10 notice string.

## Evaluation dataset

About 30 labelled questions in `tests/data/`. Data only: AGENTS.md forbids pytest files there.

**Suggested columns:** `question`, `customer_id`, `expected_chart` (`bar` / `line` / `none`), `expected_skip_reason`, `explicit_request` (bool), `expect_note` (bool).

**Cover:**

- every Skip Reason, including an injected `chart_error`;
- both sides of each limit (2/3, 8/9, 12/13);
- a mixed SQL-and-help question;
- a combined multi-SQL Sub-query question;
- a mixed-direction Result;
- each UJ-4 phrasing;
- pie-to-bar substitution;
- a balance trend for two customers.

**Run** from a cleared SQL cache, then again with the cache warm. Date-dependent labels (for example "11 months this year") go stale after a data `shift`; re-label after each shift.
