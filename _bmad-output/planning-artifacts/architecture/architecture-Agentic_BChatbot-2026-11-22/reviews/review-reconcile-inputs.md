# Review — Reconcile spine against inputs

- **Spine:** `ARCHITECTURE-SPINE.md` (FinTell baseline, draft, 2026-11-22)
- **Inputs:** `AGENTS.md`, `docs/architecture.md`, `docs/process_stream_diagram.md`, and `src/` where an input claim needed confirming
- **Lens:** load-bearing content the spine dropped, contradicts, or states wrongly, plus spine claims the code contradicts. Descriptive detail left to the code is out of scope. Leftover visualization code is treated as unused.

## Verdict

The spine covers the inputs' rules faithfully and its Structural Seed is more accurate than the input diagrams. But four rules (AD-6, AD-7, AD-13 and the Naming convention) state as current fact things the code contradicts, and two quiet constraints from AGENTS.md are missing. A builder adding a node or changing an endpoint would be misled by these. All of them can be fixed with text edits. No decision needs to be reopened.

---

## High

### H1 — AD-13 "Endpoint parity" is false today: the two endpoints handle a missing reply differently

- **Spine:** AD-13 says both endpoints "persist the same things after the graph and return the same envelope".
- **Code:**
  - `/process` dereferences `response["response_generation_response"].generated_response` with no None check (`src/app.py:453`). When it is None, the result is an `AttributeError` and a 500 `{"error": "internal_error", ...}` (`src/app.py:495-497`). Nothing is persisted.
  - `/process-stream` substitutes `TECHNICAL_FALLBACK_MESSAGE` (`src/app.py:569-572`), persists it to Redis and SQLite, and emits a normal `final` event. On an exception it emits `{"type": "error"}` (`src/app.py:604-606`), which has no counterpart in `/process`.
- **Reachable:** yes. `_supervisor_router` does not check `fallback_response` (`src/agent_graph.py:127-155`). If the supervisor raises, all three queues stay None and the graph goes to END without running `response_generator`. See H2.
- **Fix:** restate AD-13 as a target, and record the known divergence: when no reply is produced, `/process` returns a 500 and persists nothing, while `/process-stream` returns a technical-fallback message and persists it. Either fold this into the Deferred row "Merging the duplicated post-graph logic", or add a rule that both endpoints must treat a missing `response_generation_response` the same way.

### H2 — AD-7 "routers check it to divert to fallback" overstates what the code does

- **Spine:** "Failure is signalled only through `state.fallback_response`, and routers check it to divert to `fallback`."
- **Code:** only `_guardrail_router`, `_context_builder_router` and the subgraph routers read `fallback_response`.
  - `_supervisor_router` (`src/agent_graph.py:127-155`) and `_after_sql_router` (`:157-162`) ignore it.
  - A supervisor exception (`src/agents/supervisor.py:85-93`) therefore routes to END. That leads to H1.
  - A `help_center` exception (`src/agents/help_center_agent.py:101-111`) goes `help_center → join → response_generator`. It never visits `fallback`.
  - `response_generator` itself prepends `state.fallback_response.fallback_message` to the reply (`src/agents/response_generator.py:18-19`). So the `fallback` node is not the only path by which failure text reaches the user. AD-3 already says `response_generator` joins the fallback message, so AD-7 contradicts AD-3.
  - A `query_error` after correction ends the subgraph with `fallback_response` still set (`src/agent_subraph.py:122-132`). Its message, "The generated SQL query failed. Attempting automatic correction." (`src/agents/sql_execution.py:77`), is then shown to the user verbatim by `response_generator`. The spine's seed diagram draws this edge correctly ("query_error after correction → subgraph end"), but AD-7's prose implies a diversion to `fallback` that never happens.
  - `docs/architecture.md:111` (Diagram 1b) also draws this as `→ SFB`. The input is stale here, and the spine's diagram is right.
- **Fix:** reword AD-7:
  - Failure is carried in `fallback_response`.
  - Only the guardrail, context_builder and subgraph routers divert to `fallback`.
  - Any node can leave a `fallback_response` that `response_generator` will surface, so `fallback_message` must always be user-safe text.
  - A new router placed after a stage that can fail must check `fallback_response` explicitly. The supervisor gap is a known defect.

### H3 — "Node name = metrics key = trace `agent` id" (Naming convention, also AD-12) is contradicted by three existing nodes

- **Code:**
  - The outer node `suprivisor` (`src/agent_graph.py:35`) has metrics key `suprivisor` (`src/agents/supervisor.py:65`). But its trace id is `supervisor` (`src/app.py:218`), the UI icon key is `supervisor` (`src/index.html:2247`), and `fallback_node="supervisor"` (`src/agents/supervisor.py:89`).
  - The outer node `sql_generation` (`src/agent_graph.py:36`) and the inner node `sql_generation` (`src/agent_subraph.py:31`) both use metrics key `text2sql_generator` (`src/agents/text2sql_generator.py:75`). The trace id is `text2sql_generator` (`src/app.py:249`), and the step label key is `sql_generation` (`src/app.py:514`).
  - The `fallback` node writes `fallback_node="fallback_agent"` (`src/agents/fallback.py:96,106`). It overwrites the originating node, so the trace shows "Triggered at 'fallback_agent'".
  - `sql_execution` writes no metrics, and its trace entry hard-codes `metrics: None`.
- **Why it matters:** `fallback_node` is not free text. `fallback.py:46` preserves the message only when `fallback_node == "guardrail"`, and `_build_agent_trace` keys guardrail status on the same string (`src/app.py:177`). A new node that copies the "node name" convention onto `fallback_node` could change fallback behaviour.
- **Fix:**
  - Keep the convention as the rule for new nodes.
  - List the three existing exceptions explicitly.
  - Record that `fallback_node` is a matched string: `"guardrail"` is special-cased in `fallback.py` and `_build_agent_trace`.
  - Change AD-6's "with `fallback_node` set to that node" to say "the stage's metrics or trace name" and point to these exceptions.

## Medium

### M1 — Dropped: the `agent_metrics`-must-be-a-dict precondition for the subgraph (AGENTS.md:42)

- **Input:** AGENTS.md and CLAUDE.md both say the subgraph expects `agent_metrics` to already be a dict, and that a new path into the subgraph that skips `guardrail` must initialise it.
- **Code:** `_prepare_request` seeds `agent_metrics=None` (`src/app.py:419`). `text2sql_generator` and `sql_correction` write `state.agent_metrics[...]` without a None-guard (`src/agents/text2sql_generator.py:75`, `src/agents/sql_correction.py:53`). Only `guardrail`, `context_builder` and `help_center` self-initialise.
- **Spine:** silent. AD-4 covers field compatibility but not this ordering precondition.
- **Fix:** add a bullet to AD-6 or AD-4: "`agent_metrics` starts as None. A stage either None-guards before writing, or sits on a path after `guardrail`. A new path into `sql_generation` must initialise it."

### M2 — Dropped: the compile-before-logger ordering pitfall (AGENTS.md:50), which applies to both graph classes

- **Input:** `Agent_Graph.__init__` sets `self.logger` after `self.app = self._workflow()`.
- **Code:** the same is true in `Agent_SubGraph.__init__`, and it also covers `self.db_conn` and `self.redis_client` (`src/agent_subraph.py:22-25`). Neither appears in the inputs for the subgraph.
- **Spine:** absent. A builder adding a node whose wrapper reads `self.logger`, `self.db_conn` or `self.help_store` while the graph is being built would break it.
- **Fix:** add a line to AD-12, or a Consistency row: "`_workflow()` runs before `logger`, `db_conn` and `redis_client` are assigned. Node wrappers may read them only at call time, never while the graph is being built."

### M3 — AD-6 stage signature does not match four of the nine stages

- **Spine:** "Signature is `(state, llm, prompt, logger)` plus named extras."
- **Code:**
  - `input_guardrail_node(state, llm, logger)` has no prompt (`src/agents/guardrails.py:7-11`).
  - `fallback_agent(state, logger)` (`src/agents/fallback.py:39-42`).
  - `execute_query(state, db_conn, logger)` (`src/agents/sql_execution.py:10-14`).
  - `help_center_agent(state, llm, prompt, help_store, logger)` puts the extra positionally before `logger` (`src/agents/help_center_agent.py:17-23`).
- **Also:** "A stage never raises" is false for `sql_execution`. It catches only `sqlite3.Error` (`:71`). Line 20 (`state.text2sql_generator_response.sql_query.strip()`) sits outside any try. An empty SQL silently sets `sql_query_correction=True` and returns no results and no fallback (`:45-48`), which yields an empty reply.
- **Fix:** state the contract as "`state` first, `logger` last, plus whatever the stage needs (`llm`, `prompt`, `db_conn`, `help_store`, …)". Note `sql_execution` as a non-LLM stage that has no metrics and only partial exception handling.

### M4 — AD-9 / AD-11 miss that `sql_execution` commits non-SELECT statements, and that a DB rebuild wipes app-owned tables

- **Code:** if `cursor.description is None`, `sql_execution` calls `db_conn.commit()` (`src/agents/sql_execution.py:68-70`). The LIMIT safeguard applies only when the text starts with `select` (`:23`). So the app can write to `transactions`, and to every other table, through the generated SQL.
- **Effect on AD-9:** AD-9's "The `transactions` table is written only by `sqllite_db_creation.py` … The app reads it" is an intent, not a fact. AD-11 says "runs whatever SQL it receives" but not that writes are committed.
- **Rebuild:** `sqllite_db_creation.py` replaces the whole DB file (`src/clients/sqllite_db_creation.py:29-35`). That destroys `chat_*`, `request_metrics` and `query_cache`, which AD-9 says belong to `db_utils`. AGENTS.md:51 records this as a pitfall, and the spine dropped it.
- **Fix:**
  - AD-11: add "and commits it if it returns no rows. Nothing enforces read-only." (The Deferred row already names read-only SQL.)
  - AD-9: add "The builder owns the DB file. A rebuild drops all app tables, so do not keep state there that must survive a rebuild."

### M5 — AD-2 "only the shell, after the graph returns" is inaccurate

- **Code:** `_prepare_request` writes Redis before the graph runs. When history is summarised it calls `save_raw_chat_history` (`src/app.py:398-404`).
- **Effect:** the ownership claim still holds, but the timing claim is wrong.
- **Fix:** "Only the shell writes chat history and metrics: the summarised history before the graph, and the turn and metrics after it."

## Low

### L1 — AD-10 is slightly mis-scoped

- **Code:** the SQL rewrite applies only to text starting with `select` that has no aggregate (`src/agents/sql_execution.py:23-43`). So CTEs (`WITH …`) skip it. The Python hard cap (`:60-63`) applies to every row-returning query, aggregates and GROUP BY included. The trailing notice always says "most recent … transactions", even for grouped results.
- **Fix:** "All row-returning results are truncated to 20 plus a notice string. Non-aggregate `SELECT…` text is additionally rewritten with ORDER BY and LIMIT."

### L2 — The SQL cache caches pre-correction SQL, and the key is the stringified `banksql_queries` list

- **Code:** the key is `_make_cache_key(customer_id, str(state.banksql_queries))` (`src/agents/text2sql_generator.py:29-35`). It is written right after generation, before execution (`:104-115`). Corrected SQL is never written back (`src/agents/sql_correction.py:56-57`). A failing query is therefore cached and has to be corrected again on every repeat, each time paying for the `sql_correction` call. A cache hit records no `text2sql_generator` metrics.
- **Fix:** add this to the Keys row, or to the Deferred "SQL cache invalidation" row.

### L3 — Prompt loading parses the YAML as an f-string template

- **Code:** `read_prompt_file` uses `PromptTemplate.from_template` (`src/utils/file_utils.py:16`). Unbalanced braces in prompt text fail at startup. Doubled `{{ }}` would be sent literally, because the agents send `.template` raw.
- **Fix:** add one bullet to AD-8: "Prompt text is parsed as an f-string template at load. Avoid unbalanced `{}`, and do not escape braces."

### L4 — Streaming and the rate limit, which bind an endpoint change and are missing from the spine

- **Step events:** `astream` runs without `subgraphs=True` (`src/app.py:547-549`). So `step` events fire only for outer nodes, and a new node inside the subgraph gets no step label regardless of AD-12.
- **Token allowlist:** the allowlist matches `meta["langgraph_node"]` by bare name (`:563`), and the subgraph already reuses names (`fallback`, `sql_generation`). An inner node named `help_center` or `response_generator` would leak tokens.
- **Rate limit:** both endpoints carry `@limiter.limit("20/minute")`, which needs a `request: Request` parameter (`src/app.py:435,527`). AD-13 parity should include it.
- **Fix:**
  - Add a note to AD-12 that step events cover outer nodes only, and that token-allowlist names must be unique across both graphs.
  - Add the rate-limit decorator to the AD-13 parity list.

### L5 — The guardrail GREETING path and its extra LLM call

- **Code:** a greeting is routed through `fallback` with `fallback_reason="greeting"` and an LLM-generated message (`src/agents/guardrails.py:79-99`). This is a second LLM call that records no metrics. The seed diagram's label `violation` hides the fact that the happy-path greeting also goes this way.
- **Fix:** relabel the edge "violation or greeting".

---

## Places where the spine is right and the inputs are stale (no spine change)

- `docs/architecture.md:62` and `docs/process_stream_diagram.md:74` show `_context_builder_router → END` ("no queries"). The router never returns END (`src/agent_graph.py:164-172`); the END mapping is dead. The spine correctly omits it.
- `docs/architecture.md:111` and `docs/process_stream_diagram.md:84` show `error & already corrected → fallback (sub)`. The code ends the subgraph instead (`src/agent_subraph.py:125-130`). The spine's seed is correct.
- `docs/architecture.md:128` says every agent is `(state, llm, prompt, logger)` and calls the LLM. That is wrong for the same stages as M3.

## Input claims checked and confirmed against code (no finding)

- The supervisor routes to a single primary route, and `_after_sql_router` chains `help_center` sequentially (`src/agent_graph.py:127-162`).
- The subgraph is added as a node. The shared fields are present in both models (`src/app_models.py:99-124`).
- `sql_correction` runs at most once, through `sql_query_correction` (`src/agents/sql_correction.py:61`, `src/agent_subraph.py:127`).
- `cache_control` is ephemeral with a 1h TTL on all YAML-prompt agents. The guardrail prompt is hardcoded and sent without `cache_control`.
- Redis and Chroma degrade to `None` in `lifespan` (`src/app.py:73-97`). Their consumers null-check (`src/utils/file_utils.py:48,69,91`; `src/agents/help_center_agent.py:39`; `src/agents/text2sql_generator.py:37,42`).
- `agents/*` import only `app_models` and `utils` (they never import `app.py`, the graphs, or each other). `app_models.py` imports nothing project-internal.
- `MAX_RESULT_ROWS = 20`, and the cache key format and 24h Redis TTL match the spine.
