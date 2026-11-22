---
review: adversarial
target: ../ARCHITECTURE-SPINE.md
lens: 'Two units one level down that each obey every AD to the letter yet build incompatibly'
date: '2026-11-22'
grounding: [src/app.py, src/agent_graph.py, src/agent_subraph.py, src/app_models.py, src/agents/*.py, src/utils/db_utils.py, src/index.html]
---

# Adversarial review — FinTell baseline spine

## Verdict

The spine describes the current system accurately, but it is not yet a build substrate. Its rules cover a single new node added on its own. They do not cover **two** independent additions. The weakest points are the single `fallback_response` slot, the hand-wired sequential chain, the fixed envelope, and the reducer-less `agent_metrics` channel. Each of these lets two compliant stories collide.

Tiering: **critical** means two compliant units ship a wrong or contradictory user-visible answer or lose data silently. **High** means they ship an incompatible structure that someone has to refactor. **Medium/low** means drift that the trace, metrics or UI make visible.

| # | Tier | Hole | ADs involved |
|---|------|------|--------------|
| H1 | critical | One `fallback_response` slot, and every stage must write it | AD-6, AD-7, AD-3 |
| H2 | critical | No owner for sequential chain order or for class-label → branch mapping | AD-5, AD-1 |
| H3 | high | Envelope frozen by AD-13 while AD-3 requires new outputs to be serialised into it; persistence of new outputs unowned | AD-3, AD-13, AD-2, AD-9 |
| H4 | high | `agent_metrics` has no reducer, and wrappers may return partial dicts | AD-4, AD-5, AD-6 |
| H5 | medium | "Node name = metrics key = trace id = fallback_node" is asserted, but the code already breaks it, and node names are not unique across the two graphs | AD-12, AD-6, Naming |
| H6 | medium | The SQL cache rehydrates only `sql_query`, so new `Text2SqlGeneratorResponse` fields depend on whether the cache was hit | AD-2 exception, AD-4 |
| H7 | low | The order of reply segments in `response_generator` is fixed, while the streamed token order follows execution order | AD-3, AD-12 |

---

## H1 — critical: one failure slot, mandatory for every stage, read by every router

**Ground truth.**
- `Graph_State.fallback_response` is a single `FallbackResponse | None` (`app_models.py:112`). AD-6 requires *every* stage's `except` to write it. AD-7 requires routers to divert on it.
- Nothing ever clears it.
- The `fallback` node overwrites `fallback_node` with `"fallback_agent"` (`fallback.py:96`), so the origin is lost. `_build_agent_trace` then reports "Triggered at 'fallback_agent'".
- `response_generator` always prepends `fallback_message` whenever the slot is set (`response_generator.py:18-19`). It *also* appends the SQL rephrase whenever `database_query_results is not None` (`:26`).
- The routers are already inconsistent. `_context_builder_router` checks truthiness. `_guardrail_router` checks `fallback_flag`. `_supervisor_router` does not check the slot at all, so a supervisor exception routes to `END`, `response_generator` never runs, and `/process` raises on `None.generated_response` (`app.py:453`).

**Unit A** adds an optional enrichment node `X`, chained after `sql_generation`. Following AD-6, its `except` sets `fallback_response(fallback_node="X", reason="exception")`.
**Unit B** adds node `Y`, chained after `X`. Following AD-7, its router reads "if `state.fallback_response`: → `fallback`".

Both are compliant. Suppose the SQL succeeded and X has a transient failure. Then:
- Y and `help_center` are skipped.
- `fallback` runs. Its catch-all branch (`fallback.py:91`) picks the "technical issue" text.
- `response_generator` emits *"I'm sorry … technical issue … try again later.\n<correct SQL answer>"*.

The reply contradicts itself, and it is persisted to history as-is.

There is a second variant. The subgraph has already set `needs_separate_questions` (from `text2sql_generator`), and a later compliant stage fails. That stage's write replaces the specific message with a generic one. Whichever unit merges last decides which failure the user sees.

**Proposed rule (tighten AD-6/AD-7 → new AD-7a "Failure ownership and severity"):**
1. Only *gate* stages may write `fallback_response`. These are `guardrail`, `context_builder`, `suprivisor`, the subgraph stages, and the `fallback` node itself. A gate failure means "this request cannot be answered on this path".
2. Any other new stage reports failure only on **its own output field**, for example `XResponse.status: Literal["ok","degraded","error"]` plus a reason. It never writes `fallback_response`. AD-6's "never raise" still applies.
3. `fallback_node` is write-once and records the origin. The `fallback` node writes only `fallback_message` (and a `handled: bool`). It never rewrites `fallback_node`.
4. Each router diverts on `fallback_response` only if `fallback_flag and not handled`, and only for failures raised by its own upstream gate. All routers use one helper, `_failed(state) -> bool`, and `_supervisor_router` must call it before it reads the query lists.
5. The `fallback` node runs at most once per graph (outer and subgraph combined), guarded the same way `sql_query_correction` guards correction.

---

## H2 — critical: nobody owns the chain order or the label → branch table

**Ground truth.**
- The branch order is spread over three places that are each wired by hand:
  - the `if/elif` priority in `_supervisor_router` (`agent_graph.py:140-150`)
  - `_after_sql_router`, which knows only `help_center` (`:157-162`)
  - the hard edge `help_center → join` (`:83`)
- In `suprivisor_agent`, any label that is not `BankSQL` or `HelpCenter` lands in `out_of_scope` (`supervisor.py:73-78`, the `else` branch).
- `fallback_agent` treats only `banksql_queries` as "valid" (`fallback.py:52`).

AD-5 says only "chained sequentially". It does not say where a new branch goes in the chain or who edits these routers.

**Unit A** adds branch `card_services` (new `class_label` "CardServices", new field `card_queries`). It inserts the branch after help by turning `help_center → join` into `help_center → _after_help_router → {card_services | join}`.
**Unit B** adds branch `disputes` (new label "Disputes", field `dispute_queries`). It inserts the branch after SQL by changing `_after_sql_router` to return `"disputes"` when `dispute_queries` is set, with `disputes → help_center`.

Each unit, taken alone, is sequential, converges at `join`, and is compliant. Merged, they fail in four ways:
1. Each unit adds its own `elif` to `_supervisor_router`, so the primary-route priority depends on merge order.
2. A message that is help-only plus disputes never reaches `disputes`. The supervisor sends it to `help_center`, and the new edge from help only knows `card_services`.
3. Neither unit edited the supervisor loop's `else`, and the spine never says to, so the second label merged may silently become `out_of_scope` and produce the "I'm not able to help" fallback.
4. `fallback_agent`'s mixed-message texts list only BankSQL items as answerable.

**Proposed rule (new AD-5a "Branch registry"):**
1. Every branch is declared in exactly one ordered table in `agent_graph.py`: `BRANCHES = [(node_name, class_label, state_field), ...]`. Today that is `[("sql_generation","BankSQL","banksql_queries"), ("help_center","HelpCenter","help_center_queries")]`.
2. The order of the table is the execution order and the primary-route priority. A story that adds a branch states its position in the table explicitly.
3. One generic router, `_next_branch(state, after=<node>)`, returns the next branch in the table whose `state_field` is non-empty, or `join`. Every branch's outgoing edge uses it. Hard edges to `join` from a branch node are forbidden.
4. `suprivisor_agent` builds its label → field mapping from the same table. An unmapped label raises inside its `try` (becoming a gate failure under H1), and is never filed silently as `out_of_scope`.
5. `fallback_agent` takes "answerable" items from all branch fields in the table, not just from `banksql_queries`.
6. Adding a label to `ClassifiedSubQuery.class_label` and `prompts/suprivisor_prompt.yml` is part of the same change (extend AD-9's sync rule to cover class labels).

---

## H3 — high: envelope frozen by AD-13, expanded by AD-3; new-output persistence unowned

**Ground truth.**
- AD-13 fixes the envelope as `{response, visualization, agent_trace}`. AD-3 says any new user-facing output is a `Graph_State` field that "the endpoints only serialise into the envelope". The spine never says *how*: a new top-level key, nesting under an existing key, or a generic container.
- Both endpoints build the payload inline and duplicate each other (`app.py:480-492` and `:586-596`). The parity is already broken: if `response_generator` fails, `/process` raises (`:453`), while `/process-stream` substitutes `TECHNICAL_FALLBACK_MESSAGE` and still persists (`:569-572`).
- `index.html:1930` hard-codes the envelope keys in its reconcile fallback.
- Only reply *text* reaches `chat_messages`, so `/chat-history` can never replay a non-text output.

**Unit A** adds output type `foo` (a `foo_response` field). Reading AD-13 literally (same keys), it nests `foo` inside an `agent_trace` entry's `details`, or reuses the always-`null` `visualization` slot.
**Unit B** adds output type `bar` and, reading AD-3 literally, adds a top-level `bar` key to both endpoints.

Both are compliant, and the result is two incompatible envelope conventions that the UI must special-case.

Persistence collides as well. Both want their output to survive a reload. Under AD-9, everything goes "through `db_utils`":
- A adds a `foo_json` column to `chat_messages`.
- B adds a `bar_outputs` table keyed by `session_id`.

That gives two owners of "assistant-message attachments", and `delete_other_sessions` cleans up only one of them.

**Proposed rule (tighten AD-3 + AD-13 → AD-13a "Envelope and output registry"):**
1. The envelope is `{response, visualization, agent_trace, outputs}`, where `outputs: {<output_key>: payload | null}`. New output types go **only** into `outputs` and never become new top-level keys. `visualization` is reserved and stays unused until its own architecture decides otherwise.
2. One function, `_build_envelope(final_state) -> dict`, is used by both endpoints. It owns the `response` text default (`TECHNICAL_FALLBACK_MESSAGE` when `response_generation_response` is `None`), and it calls one registered serializer per output type. Each serializer returns `None` when its field is absent and never raises.
3. Persistence of the reply plus outputs happens in one shell helper, `_persist_turn(final_state, ...)`, shared by both endpoints. New outputs are persisted, if at all, as JSON in a single `chat_messages.outputs` column (owned by `db_utils`). No per-output tables.
4. A new output story declares three things: state field, `output_key`, and persisted yes/no.

(This moves the "merge duplicated post-graph logic" item out of Deferred. Its revisit trigger, "envelope next changes", is exactly what the first new-output story does.)

---

## H4 — high: `agent_metrics` is last-writer-wins, and the spine allows partial returns

**Ground truth.**
- `agent_metrics: dict[str, Metrics] | None` has no reducer in either model (`app_models.py:115,124`). The channel survives today for three reasons:
  - every stage mutates the dict in place
  - every wrapper returns the *whole* state
  - the subgraph returns its full (validated-copy) dict, which is a superset
- The `_fallback` wrappers in both graphs already return a **partial** dict (`agent_graph.py:214-216`, `agent_subraph.py:155-157`), so the codebase demonstrates both return styles.
- AD-6 says "returns the state", but never forbids the partial style. AD-4 compares only names and types, not merge semantics.
- `db_utils.save_metrics` reads attributes (`m.model_used`, …; `db_utils.py:200-216`), so a value that is not a `Metrics` object breaks persistence.
- `response_generator` assumes the dict exists (`:63`), while `help_center` defensively creates it (`:91-92`).

**Unit A** adds a subgraph stage (say, a SQL validator) whose wrapper copies the `_fallback` pattern and returns `{"agent_metrics": {"sql_validator": m}}`. That replaces the whole channel when the subgraph hands back to the outer graph, and the `guardrail`, `context_builder`, `suprivisor` and `text2sql_generator` metrics disappear.
**Unit B** adds a cost dashboard on top of `request_metrics`, assuming that every LLM stage of a request is present.

Both are compliant. B's numbers are silently wrong whenever A's node runs.

A second variant: a non-LLM stage records `{"latency": t}` as a plain dict, "metrics via `agent_metrics()`" being impossible without an `AIMessage`. Then `save_metrics` raises *after* Redis history was saved, and `/process` returns 500 with persistence half done. AD-2 exists to prevent exactly that.

**Proposed rule (tighten AD-4/AD-6 → AD-6a "Shared channel writes"):**
1. Declare `agent_metrics: Annotated[dict[str, Metrics], merge_dicts] = {}` in **both** models, with one reducer shared from `app_models.py`. Then no node, wrapper or subgraph can clobber it, and the "guardrail initialises it" gotcha goes away.
2. Until the reducer exists, every wrapper of a node that touches `agent_metrics` returns the full state. Partial-dict returns are allowed only for nodes that write no channel shared with the subgraph.
3. Values are always `Metrics` instances produced by `agent_utils.agent_metrics()`. Stages without an LLM call write no metrics entry.
4. Extend AD-4: a field shared between the two models has the same name, type, **default and reducer**. Every new field has a default, because `_prepare_request` builds `Graph_State` by keyword and LangGraph builds `SubGraph_State` from the overlapping keys.

---

## H5 — medium: the canonical-id convention is false today and not unique across graphs

**Ground truth.**
- The Naming row claims node name = metrics key = trace `agent` id. In the code:
  - The outer node is `suprivisor`, but its trace id is `supervisor` and its `fallback_node` is `"supervisor"` (`supervisor.py:89`).
  - The inner text2sql node is named `sql_generation`, the same as the outer subgraph node, but its metrics key is `text2sql_generator`.
  - The `fallback` node stamps `fallback_node="fallback_agent"`.
  - Both graphs have a node called `fallback`.
- `/process-stream` filters tokens on `meta["langgraph_node"]`, which carries the *inner* node name for subgraph nodes (`app.py:563`).
- AD-6 says "`fallback_node` set to that node" without saying whether "that node" means the node name or the agent name.

**Unit A** adds a router (or trace logic) that keys on `fallback_node == "suprivisor"`, the node name, as the Naming row says.
**Unit B** adds a stage that, copying `supervisor.py`, writes its agent name rather than its node name into `fallback_node`. Both are compliant, but A's branch never fires.

Likewise, a streaming stage added *inside* the subgraph under a name that also exists in the outer graph (e.g. `fallback`) is matched by the token allowlist, the step labels and the trace for both nodes.

**Proposed rule (tighten AD-12 + Naming):** Every node has one canonical id, unique across **both** graphs. That id is the node name and is also used verbatim for the metrics key, `fallback_node`, trace `agent`, the `_STREAM_STEP_LABELS` key and the allowlist entry. The existing exceptions (`suprivisor`↔`supervisor`, inner `sql_generation`↔`text2sql_generator`, `fallback_agent`, the duplicated `fallback`) are listed once in an alias table in the spine as frozen legacy. New nodes may not add aliases.

---

## H6 — medium: cache rehydration erases new response fields

**Ground truth.**
- The cache stores only the SQL string. On a hit, `text2sql_generator` rebuilds `Text2SqlGeneratorResponse(user_query=..., sql_query=cached_sql)` (`text2sql_generator.py:49-53`). The SQLite `query_cache` never expires.
- A cache hit also writes no `text2sql_generator` metrics.
- AD-2 sanctions the cache, but no rule covers the shape of what is cached.

**Unit A** adds a field, e.g. `referenced_columns: list[str] = []`, to `Text2SqlGeneratorResponse` and fills it from the LLM.
**Unit B** adds a downstream stage (or output serializer) that uses `referenced_columns`.

Both are compliant (AD-4 is satisfied because both models share the type). Yet B behaves differently depending on whether the cache was hit: correct on a miss, empty on every hit, forever, for every query cached before A shipped. Had A made the field required, every cache hit would raise and turn into a fallback.

**Proposed rule (extend the AD-2 exception):** The SQL cache stores the full `Text2SqlGeneratorResponse` as JSON, together with a schema/prompt version in the key or row, and rehydrates by `model_validate_json`. Rows with an older version count as misses. New fields on any cached model must be optional with a default, and consumers must treat the default as "unknown", not as "empty".

---

## H7 — low: reply segment order vs. streamed token order

**Ground truth.**
- `response_generator` concatenates in a fixed order: fallback → help → SQL rephrase (`response_generator.py:17-69`).
- Tokens stream in *execution* order from allowlisted nodes (`help_center`, then `response_generator`).
- The UI shows the tokens as they arrive, then replaces the bubble with `final.response` (`index.html:1916-1949`).
- The two orders agree today only by coincidence.

**Unit A** adds a streaming branch `Z` that sits before `help_center` in the chain and registers it in the allowlist (AD-12).
**Unit B** (or A's own edit to `response_generator`, which AD-3 requires) appends Z's text *after* the help answer.

The user watches the reply type out as Z then help, and it then jumps to help then Z when `final` arrives. The fallback text is never streamed at all.

**Proposed rule (add to AD-3):** `response_generator` assembles segments in branch-registry order (H2), with the fallback message first. Any node in the token allowlist must be one whose text `response_generator` passes through unchanged, so the streamed text is a prefix of `final.response` in the same order.

---

## Not holes (checked and held)

- **Parallel writes:** AD-5 does forbid fan-out, and the current routers return a single route each.
- **Prompt handling:** AD-8 is concrete enough that two prompt-adding stories can't diverge.
- **Customer scoping:** AD-11 records honestly that scoping is prompt-only. Two units can't make it *worse* without editing the prompt, which the rule forbids.
- **Singletons:** AD-14 holds as long as stages receive `llm` from the wrapper. Note that the per-request compile in `_prepare_request` means a hot-swap mid-request is impossible by construction, which is fine.
