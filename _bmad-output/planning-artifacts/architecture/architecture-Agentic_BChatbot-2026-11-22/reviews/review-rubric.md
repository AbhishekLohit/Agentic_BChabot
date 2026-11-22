---
reviews: ARCHITECTURE-SPINE.md (FinTell baseline, altitude: feature, status: draft)
method: good-spine rubric, every AD claim checked against src/, prompts/, requirements.txt, AGENTS.md
date: 2026-11-22
---

# Spine review — FinTell baseline

## Verdict

The spine has a sound structure and covers most of the real divergence points. It needs one revision before stories build on it, because several [ADOPTED] rules describe the code as it should be rather than as it is. The biggest gaps are the failure and convergence contract (AD-5, AD-7, AD-13), the naming identity in AD-12 and the Naming convention, and the stage contract (AD-6). The operational envelope is only partly covered: tracing, rate limiting, cost accounting and provider portability are not mentioned.

## Rubric scorecard

| Check | Result | Notes |
| --- | --- | --- |
| Fixes the real divergence points, misses none | Partial | Missing: "every path ends at `response_generator`", the `fallback_node`/`fallback_reason` vocabulary, rate-limit policy for endpoints, and the metrics cost model. |
| Every Rule enforceable and prevents its divergence | Partial | AD-9 ("the app reads it") cannot be enforced while AD-11 admits arbitrary SQL with commit on the shared connection. AD-12's "metrics key equals node name" is already false. |
| Nothing in Deferred lets units diverge | Partial | Merging the post-graph logic is deferred on the grounds that AD-13 parity holds, but parity is already broken. SQL-cache invalidation is deferred with no rule attached. |
| Named tech verified-current | Pass | Every pinned version matches `requirements.txt`. `claude-sonnet-4-6` and `text-embedding-3-small` match `app_setting.py`. Plotly.js 2.35.2 matches `index.html:10`. The unpinned langchain-anthropic/genai/groq are stated honestly. |
| Ratifies, doesn't contradict, the brownfield code | Fail (several ADs) | See findings 1–3. |
| Altitude dimensions decided / deferred / open | Partial | Deployment and scaling are covered by AD-14 and Deferred. Observability (LangSmith), rate limiting, cost accounting and provider portability are not mentioned at all. |
| Mermaid valid, no template leftovers, no placeholders | Pass | All three diagrams parse. The seed flowchart honestly draws `SUP -->|nothing| END1`, which contradicts the AD-5 text (finding 1). The ER diagram uses a conceptual `customer` entity that has no table; consider a note saying so. |

## Findings

### 1. [HIGH] The failure and convergence contract (AD-5, AD-7, AD-13) contradicts the code, and the invariant that matters is never stated

**Evidence**
- AD-5 says "Everything converges at `join` before `response_generator`". But `_supervisor_router` (`src/agent_graph.py:152-153`) returns `END` when all three query lists are empty, which skips `join` and `response_generator`. The seed diagram shows this edge.
- AD-7 says "routers check [`fallback_response`] to divert to `fallback`". But `_supervisor_router` never reads `fallback_response`. When `suprivisor_agent` throws, it sets `fallback_response` (`src/agents/supervisor.py:85-93`) and leaves the lists `None`, so the router goes to `END`.
- On a second `query_error` (after `sql_correction` has run), `_sql_execution_router` (`src/agent_subraph.py:122-132`) routes to `end`, not `fallback`. The internal message "The generated SQL query failed. Attempting automatic correction." (`src/agents/sql_execution.py:77`) then flows through `response_generator`, which joins `fallback_message` into the reply (`src/agents/response_generator.py:18-19`), so the user sees it.
- AD-13 says both endpoints "persist the same things". But when `response_generation_response` is `None` (the END path above, or a `response_generator` exception at `response_generator.py:71-79`), the two endpoints behave differently:
  - `/process` raises an `AttributeError` at `src/app.py:453` and returns a 500 with `{"error","details"}`. It persists nothing.
  - `/process-stream` substitutes `TECHNICAL_FALLBACK_MESSAGE` (`app.py:569-572`) and persists that message to Redis and SQLite.
- The Deferred row "Merging the duplicated post-graph logic … Parity rule covers it for now" rests on parity that does not hold.

**Why it matters at feature altitude:** every new node or branch has to decide where its failure goes and whether the request still reaches `response_generator`. The spine gives builders a rule the code does not follow, so new branches will copy whichever pattern they happen to read.

**Fix**
- Add an invariant: "Every path reaches `join → response_generator`. `END` is reachable only from `response_generator`. `response_generation_response` is always set when the graph returns." Have `_supervisor_router` route "nothing" to `fallback`.
- Add a router rule to AD-7: "Every router checks `state.fallback_response` first." Add a subgraph rule: "A second `query_error` routes to `fallback`, never `end`."
- If you would rather keep the baseline strictly as-is, record the three deviations in a "Known deviations from AD-5/AD-7/AD-13" list instead.
- Either way, move "merge post-graph logic" out of Deferred, or reword its rationale. The `None`-response handling has to be identical in both endpoints now.

### 2. [HIGH] "Node name = metrics key = trace `agent` id" (Naming convention, AD-12) is false for existing nodes, and the `fallback_node` vocabulary is not fixed

**Evidence**
- The supervisor uses three different identifiers:
  - node `"suprivisor"` (`agent_graph.py:35`)
  - metrics key `"suprivisor"` (`supervisor.py:65`)
  - trace agent `"supervisor"` (`app.py:218`) and icon key `supervisor` (`index.html:2247`)
- It also sets `fallback_node="supervisor"` (`supervisor.py:89`).
- Text-to-SQL uses three more:
  - outer node `"sql_generation"` (the subgraph), with the inner node also named `"sql_generation"` (`agent_subraph.py:31`)
  - metrics key and trace agent `"text2sql_generator"` (`text2sql_generator.py:75`, `app.py:249`)
  - SSE step label keyed `"sql_generation"` (`app.py:514`)
- The fallback node is named `"fallback"`, but `fallback_agent` writes `fallback_node="fallback_agent"` (`fallback.py:96,106`). It overwrites the originating node, so the trace's "Triggered at '…'" (`app.py:319`) always reads `fallback_agent` after any non-guardrail fallback.
- Branching code depends on these values. `fallback_agent` checks `fallback_node == "guardrail"` (`fallback.py:46`), `_build_agent_trace` checks `== "guardrail"` (`app.py:177`), and the routers check `fallback_reason` values `"query_error"` and `"needs_separate_questions"`. The Errors convention only says the reason is "such as `exception` or `query_error`", which leaves the vocabulary open.

**Why it matters:** these identifiers are how units find each other across the code: routers, the trace, the icon map, the SSE labels and `request_metrics` rows. A new node that follows the stated convention will be correct. But anyone extending an existing node, or writing a metrics or trace consumer, will key on the wrong string. A new `fallback_reason` that the `fallback_agent` branch ladder doesn't handle will fall through to the generic "technical issue" message without any error.

**Fix**
- Restate the convention as a rule for new nodes only, and add a table of the existing exceptions (supervisor, sql_generation/text2sql_generator, fallback_agent).
- Add `fallback_node` (= the node name of the stage that raised it) and a closed `fallback_reason` list to the Errors convention: `exception`, `query_error`, `needs_separate_questions`, `mixed_valid_invalid`, `multiple_valid`, `multiple_invalid`.
- Require any new reason to be handled in `fallback_agent` and in the routers.
- State whether `fallback_agent` may overwrite `fallback_node`. Preferably it may not.

### 3. [MEDIUM] AD-6 (stage contract) and the Models convention are written as [ADOPTED] but most stages don't follow them

**Evidence**
- Signatures do not follow `(state, llm, prompt, logger)` plus extras:
  - `input_guardrail_node(state, llm, logger)`, with no prompt (`guardrails.py:7`)
  - `fallback_agent(state, logger)` (`fallback.py:39`)
  - `execute_query(state, db_conn, logger)`, annotated `-> list[str]` (`sql_execution.py:10-14`)
  - `help_center_agent(state, llm, prompt, help_store, logger)`, with the extra before `logger` (`help_center_agent.py:17-23`)
- "The whole body is in try/except … A stage never raises" does not hold for `execute_query`. The LIMIT rewriting and `state.text2sql_generator_response.sql_query.strip()` sit outside the `try` (`sql_execution.py:20-48`), and only `sqlite3.Error` is caught.
- "Agent modules … with `<name>_agent` functions" is contradicted by `input_guardrail_node`, `execute_query` and `sql_correction`.
- "A stage's output is one `<Stage>Response` model stored on one state field" is contradicted three times:
  - `help_center` stores a `ResponseGeneratorResponse` in `help_center_response`
  - `suprivisor` writes four fields
  - `sql_execution` writes `list[str]`

**Fix:** keep AD-6 as the rule for new stages, and list the existing exceptions explicitly (non-LLM stages take `(state, <resource>, logger)`, plus the module and function names). Add "unexpected exceptions too, not only domain errors" to the try/except clause. Reword the Models convention to "a stage's primary output is a Pydantic model in `app_models.py`", or list the exceptions. Otherwise a reviewer cannot tell a new violation from an accepted one.

### 4. [MEDIUM] AD-9's ownership rule cannot be enforced as written, and the DB rebuild breaks it too

**Evidence**
- AD-9 says the `transactions` table "is written only by `sqllite_db_creation.py` … The app reads it". But `execute_query` runs any SQL and calls `db_conn.commit()` for non-SELECT statements (`sql_execution.py:68-70`). It does this on the one shared connection (`app.py:106`), which also holds `chat_*`, `request_metrics` and `query_cache`. So an LLM-generated DML statement could write any table, which also breaks the second bullet (app tables written "only through `utils/db_utils.py`"). AD-11 admits that execution is unrestricted. The two ADs contradict each other.
- AGENTS.md:51 notes that rebuilding the DB replaces the whole file, which wipes the `chat_*`, `request_metrics` and `query_cache` tables. So the builder destroys the app tables too.
- A change to the `transactions` schema or the text2sql prompt leaves stale cached SQL behind (AGENTS.md:52). AD-9 makes such a change update both prompts, but it does not require clearing the cache. The matching Deferred row has no interim rule.

**Fix:** choose one of these.
- (a) The cheap enforcement, which stays within the local-demo scope. Give `sql_execution` a separate read-only connection (`sqlite3.connect("file:transactions.db?mode=ro", uri=True)` or `PRAGMA query_only=ON`), and make AD-9 a real rule.
- (b) Reword AD-9 to "intended ownership; not enforced (see AD-11)".

In either case, add two things to AD-9: "a schema or text2sql-prompt change also clears `query_cache` and `qcache:*`", and a note that rebuilding the DB wipes the app tables.

### 5. [MEDIUM] Parts of the operational and provider envelope are not mentioned

**Evidence**
- **Observability:** `lifespan` turns on LangSmith tracing when `LANGCHAIN_API_KEY` is set (`app.py:60-64`; `app_setting.py:37-39`). The spine never mentions tracing, so there is no decision on whether new nodes or extra LLM calls must be traceable.
- **Rate limiting:** both process endpoints use `@limiter.limit("20/minute")` (`app.py:435,527`). `/update-settings`, `/chat-history` and `/login` have no limit. slowapi appears in Stack, but no rule says whether new endpoints are rate-limited, or that the limit is part of AD-13 parity.
- **Cost accounting:** `agent_metrics()` hard-codes Claude Haiku 4.5 per-token prices (`utils/agent_utils.py:37-41`). The default model is `claude-sonnet-4-6`, and the provider can be hot-swapped. So every `model_cost_*` value saved to `request_metrics` is wrong. AD-6 makes all stages use this function, but the spine never decides how cost is priced per model.
- **Provider portability:** AD-8 has every agent send Anthropic-style `cache_control` content blocks. `message_to_text` assumes Anthropic-style content lists. Structured output also behaves differently across providers. Yet AD-14 and CLAUDE.md treat `openai`, `gemini` and `groq` as swappable at runtime. The spine does not say whether non-Anthropic providers are supported, best-effort, or untested. That decides whether a new stage has to be checked against all four providers.

**Fix:** add one short "Operations" AD or Deferred block that covers:
- tracing: LangSmith, opt-in by env var; new LLM calls go through LangChain so they are traced automatically
- the rate-limit policy for new endpoints
- the cost model: pricing looked up by `model_used`, or "costs are indicative, Haiku 4.5 prices", listed as a known defect
- a provider-support statement: Anthropic is primary, the others are best-effort, `cache_control` is Anthropic-only

Alternatively, record each of these as an open question.

## Lower-priority notes

- **[LOW]** AD-14: `/update-settings` logs through `logging.getLogger("app_logger")` rather than the injected logger (`app.py:673-674`). The swap is also global and unauthenticated, and `/update-settings` already appears in the Deferred hardening row. Adding "applies to the next request only; requests already running keep their LLM" would make the hot-swap meaning explicit for builders.
- **[LOW]** `history_summarizer` uses `include_raw=False` and records no metrics (`history_summarizer.py:46`). It is not a graph node, but AD-6 or the Naming convention should say whether shell-side LLM calls are exempt from metrics.
- **[LOW]** The Stack table leaves out `python-dotenv` 1.2.1 and `PyYAML` 6.0.3, which the Config convention and AD-8 rely on, and `openai` 2.21.0. Add them for completeness.
- **[LOW]** AD-15 covers startup only. Say whether a Redis outage *after* startup (with the handle still set) must also degrade. Today the consumers wrap Redis calls in try/except in `text2sql_generator`, but the spine doesn't require it.

## What the spine gets right

- The paradigm and the layer table match the code, and the dependency direction holds (`agents/*` import neither `app.py` nor each other).
- AD-4 is accurate: the overlapping `Graph_State`/`SubGraph_State` fields have the same names and types.
- AD-10 is accurate: there is the regex LIMIT and clamp, plus a hard Python cap with a trailing notice string.
- AD-11 is an honest as-is record with a sensible Deferred trigger.
- AD-2's sanctioned cache exception, AD-14's singletons and AD-15's startup degradation all match `lifespan` and `text2sql_generator`.
- The note on unused visualization code is correctly kept out of the design.
- The versions are accurate.
