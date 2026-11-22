<!-- bmad:context -->
<!-- Verified 2026-11-22 against 3bac867. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## Agentic_BChatbot (FinTell)

FastAPI + LangGraph backend and single-page chat UI (`src/index.html`) for a banking-transactions chatbot: a query passes a guardrail, is split and classified, then answered by text-to-SQL over a SQLite `transactions` table or by strict RAG over a Barclays help-centre Chroma store, and is rephrased into a chat reply. Python 3.11+. Default LLM is Anthropic `claude-sonnet-4-6`; embeddings always use OpenAI `text-embedding-3-small`. Diagrams, agent and endpoint reference: `docs/architecture.md`. BMAD planning output: `_bmad-output/`.

## Where things are

- Outer graph: `src/agent_graph.py` (routers `_guardrail_router`, `_context_builder_router`, `_supervisor_router`, `_after_sql_router`); SQL subgraph: `src/agent_subraph.py`.
- Both graph states: `src/app_models.py`. Request setup shared by `/process` and `/process-stream`: `_prepare_request` in `src/app.py`.
- Agents: `src/agents/`, one async function each. Prompts: `prompts/*.yml`, wired in `src/app_setting.py`.
- Adding or changing an agent, endpoint, or data file? Read the reference section of `docs/architecture.md` first.

## Running and verifying

- Start the app with `python src/app.py` from the repo root; never `uvicorn src.app:app` — imports assume `src/` is on `sys.path`. `DEV_MODE=true` reloads on `src/` and `prompts/`.
- Redis is optional; without it the app starts with chat-history persistence and the Redis SQL cache disabled.
- `.env` needs the active provider's key and `OPENAI_API_KEY` regardless of provider (help-store embeddings). `LLM_PROVIDER` in `.env` overrides the default in `src/app_setting.py`; `POST /update-settings` hot-swaps it at runtime.
- There is no test suite; never add pytest files under `tests/data/`, which holds evaluation datasets kept as data. Verify by running the app and calling `/process`.
- Rebuild the DB: `python src/clients/sqllite_db_creation.py --db transactions.db --xlsx data/data_processing/df_transaction_data_enhanced.xlsx`.
- Rebuild the help store: `python src/clients/barclays_help_ingest.py` (writes `help_store/` and `help_chunks.xlsx`).
- Data pipeline: run `data/transaction_data_preprocess.py` from inside `data/data_processing/` — it uses cwd-relative paths; then `python data/data_processing/transaction_data_enhance.py build`, then `shift`.
- `shift` rewrites only the enhanced `.xlsx`; rebuild `transactions.db` afterwards or the app keeps the old dates.

## Conventions that differ from defaults

- Agent signature is `(state, llm, prompt, logger)` plus extras; it mutates and returns state. The graph classes are thin wrappers.
- Structured LLM calls use `llm.with_structured_output(<Model>, include_raw=True)` so the raw `AIMessage` feeds metrics; `response_generator` and `help_center` stream plain text instead so `/process-stream` can emit tokens.
- Store metrics via `utils.agent_utils.agent_metrics()` under `state.agent_metrics["<agent_name>"]`.
- Wrap each agent body in `try/except` that sets `state.fallback_response = FallbackResponse(fallback_reason="exception", fallback_node=<name>, ...)`; routers send that to `fallback`. Never raise out of an agent.
- Prompts are YAML with a single `prompt:` key. Pass `prompt.template` as the system message and build the user message with f-strings; never call `PromptTemplate.format`.
- Adding a prompt means extending `AppSettings`, `Prompts`, and `get_all_prompts` together.
- System prompts are sent with `cache_control` (ephemeral, 1h); editing prompt text is fine, but expect a cache miss on the next call.
- Use the one shared SQLite connection from `app.state` via `src/dependencies.py`; never open a new connection in an agent.

## Cross-component rules

- The SQL subgraph is added as a node of the outer graph; any field the subgraph reads or writes (`text2sql_generator_response`, `database_query_results`, `sql_query_correction`, `banksql_queries`, `agent_metrics`, …) must be changed in both `Graph_State` and `SubGraph_State`.
- `sql_correction` runs at most once: `_sql_execution_router` checks `state.sql_query_correction`. Keep that check when touching the subgraph.
- `_supervisor_router` picks one primary route (BankSQL, else help, else fallback); `_after_sql_router` chains `help_center` after SQL so the two never write `agent_metrics` concurrently. Never make them parallel; `join` syncs before `response_generator`.
- The subgraph expects `agent_metrics` to already be a dict (`guardrail` sets it). A new path into the subgraph that skips `guardrail` must initialize it.
- SQL targets only `transactions(transaction_id PK, customer_id, card_number, transaction_date, year, month, day, amount_usd, transaction_type, balance, merchant_brand_name, merchant_system_category1_name, merchant_system_category2_name)`.
- `amount_usd` is always positive; direction comes from `transaction_type` (`income`, `payment`, `direct_debit`, `standing_order`). `balance` is the running balance after each row.
- On a schema change, update `prompts/suprivisor_prompt.yml` and `prompts/text2sql_generator.yml` to match.

## Known pitfalls

- Never "fix" the `suprivisor` misspelling in passing — it is load-bearing across the prompt filename, `Prompts.suprivisor_prompt`, the agent module and function, and `Graph_State.suprivisor_response`.
- `Agent_Graph.__init__` sets `self.logger` after `self.app = self._workflow()`; never make compile call a node method that needs the logger.
- Rebuilding the DB replaces `transactions.db` (old copy backed up with a timestamp), wiping the `chat_*`, `request_metrics`, and `query_cache` tables too.
- Generated SQL is cached by customer + normalised query text only (`_make_cache_key` in `src/agents/text2sql_generator.py`): `query_cache` rows never expire and Redis `qcache:*` keys live 24h. After changing the text2sql prompt or the schema, run `DELETE FROM query_cache` and delete the `qcache:*` Redis keys, or repeated questions keep getting the old SQL.

<!-- /bmad:context -->
