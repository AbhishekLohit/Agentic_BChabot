# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A FastAPI + LangGraph backend for a **banking-transactions chatbot** (Barclays-style "FinTell"). It accepts a natural-language user query, runs it through a security guardrail, splits/classifies it, then either (a) generates SQL against a single SQLite `transactions` table, executes it, or (b) answers a how-to question from a Barclays help-centre RAG store. The result is rephrased into a chat reply.

The default LLM provider is **Anthropic** (`claude-sonnet-4-6`), set by `LLM_PROVIDER` in [src/app_setting.py](src/app_setting.py). The provider is hot-swappable at runtime via `POST /update-settings` — supported values are `"openai"`, `"anthropic"`, `"gemini"`, `"groq"`. `text-embedding-3-small` (OpenAI) is the embedding model used by the help-centre store.

## Common commands

All commands are run from the repo root. The server must be run with `src/` on the import path — `python src/app.py` works because Python prepends the script's directory to `sys.path`.

```powershell
# Install dependencies (Python 3.11+)
pip install -r requirements.txt

# Run the FastAPI app on 0.0.0.0:8000 (set DEV_MODE=true for reload on src/ and prompts/).
# Redis is optional: if it is unreachable the app starts with chat-history persistence disabled.
python src/app.py

# (Re)build transactions.db from the enhanced Excel.
# Backs up any existing DB with a timestamped suffix — note this REPLACES the live DB,
# including the chat_*, request_metrics and query_cache tables created by db_utils.
python src/clients/sqllite_db_creation.py --db transactions.db `
    --xlsx data/data_processing/df_transaction_data_enhanced.xlsx

# (Re)build the Barclays help-centre Chroma store (help_store/) and dump chunks to help_chunks.xlsx
python src/clients/barclays_help_ingest.py

# Data pipeline (raw -> preprocessed -> enhanced). `shift` re-bases dates so the latest txn is today.
python data/data_processing/transaction_data_enhance.py build
python data/data_processing/transaction_data_enhance.py shift
```

`.env` at the repo root must contain the API key for whichever provider is active (e.g. `ANTHROPIC_API_KEY`) **and** `OPENAI_API_KEY` (embeddings for the help store). It is gitignored and read by `clients/llm_client.py` via `python-dotenv`. See `.env.example`.

There is no test suite. `tests/data/` only holds hand-curated evaluation datasets (golden set + context-builder eval sets) kept as data.

## Architecture

### Request flow

`POST /process` and `POST /process-stream` share `_prepare_request()` in [src/app.py](src/app.py), which loads chat history (Redis, seeded from the last 24h of SQLite messages for a fresh conversation, summarised once there are ≥5 prior user turns), builds a `Graph_State`, and compiles the graphs per request. There are **two** graphs:

- **Outer graph** ([src/agent_graph.py](src/agent_graph.py)) — `guardrail → {context_builder | fallback} → suprivisor → {sql_generation subgraph | help_center | fallback | END} → (help_center if the message also had a help query) → join → response_generator → END`. Routing is in `_guardrail_router`, `_context_builder_router`, `_supervisor_router`, and `_after_sql_router`.
- **Inner subgraph** ([src/agent_subraph.py](src/agent_subraph.py)) — `sql_generation → sql_execution → {END | sql_correction → sql_execution | fallback}`. `sql_correction` runs at most once: `_sql_execution_router` checks `state.sql_query_correction` to avoid an infinite loop.

The outer graph **adds the subgraph as a node** (`builder.add_node("sql_generation", self.agent_subgraph.app)`), so both states must remain field-compatible where they overlap. `Graph_State` and `SubGraph_State` are both defined in [src/app_models.py](src/app_models.py); changes to fields the subgraph reads/writes (e.g. `text2sql_generator_response`, `database_query_results`, `sql_query_correction`, `banksql_queries`, `agent_metrics`) must be made in both models.

### Agents

Each agent is a stand-alone async function in `src/agents/` that takes `(state, llm, prompt, logger)` (plus extras where noted), mutates the state, and returns it. The graph classes are thin wrappers. Conventions:

- LLM calls use `llm.with_structured_output(<PydanticModel>, include_raw=True)` so the response is both parsed and the raw `AIMessage` is available for metrics. (`response_generator` and `help_center` stream plain text instead so `/process-stream` can surface tokens.)
- Metrics are computed by `utils.agent_utils.agent_metrics()` (token counts, latency, cost) and stored under `state.agent_metrics["<agent_name>"]`.
- The whole body is wrapped in `try / except` that sets `state.fallback_response = FallbackResponse(fallback_reason="exception", fallback_node=<name>, ...)`. Routers then steer the flow to the fallback node.

| Agent | File | Role |
|---|---|---|
| `guardrail` | [src/agents/guardrails.py](src/agents/guardrails.py) | First node. LLM-based input check (SYSTEM / SQL / PII violation, or GREETING). Prompt is hardcoded in the function — no YAML. Initializes `agent_metrics = {}`. |
| `context_builder` | [src/agents/context_builder.py](src/agents/context_builder.py) | Splits the query + chat history into one or more self-contained `contextual_queries`. |
| `supervisor` | [src/agents/supervisor.py](src/agents/supervisor.py) | Classifies each sub-query into `banksql_queries`, `help_center_queries` or `out_of_scope_queries`. |
| `text2sql_generator` | [src/agents/text2sql_generator.py](src/agents/text2sql_generator.py) | NL → SQL, with a Redis + SQLite (`query_cache`) cache keyed on customer + query. |
| `sql_execution` | [src/agents/sql_execution.py](src/agents/sql_execution.py) | Runs the SQL; auto-adds/clamps `LIMIT` to `MAX_RESULT_ROWS` (20). |
| `sql_correction` | [src/agents/sql_correction.py](src/agents/sql_correction.py) | Repairs a failed SQL query (runs at most once). |
| `help_center` | [src/agents/help_center_agent.py](src/agents/help_center_agent.py) | Strict-RAG answer from `HelpStore` (Chroma, `help_store/`); grounded "couldn't find it" reply when nothing clears the similarity threshold. |
| `response_generator` | [src/agents/response_generator.py](src/agents/response_generator.py) | Rephrases SQL results (and passes through help answers) into the final reply. |
| `fallback` | [src/agents/fallback.py](src/agents/fallback.py) | Turns `state.fallback_response` into a user-visible message (no LLM, no prompt). |
| `history_summarizer` | [src/agents/history_summarizer.py](src/agents/history_summarizer.py) | Not a graph node; called from `_prepare_request` to compress long chat history. |

### API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves `src/index.html` (the chat UI). |
| `POST` | `/login` | Validates `user_id` + `password` against env vars `DASHBOARD_USER_IDS` / `DASHBOARD_PASSWORD`. |
| `POST` | `/process-stream` | SSE variant used by the UI: emits `step`, `token`, then a `final` event (`response`, `visualization`, `agent_trace`). |
| `POST` | `/process` | Non-streaming equivalent. Returns `{response, visualization, agent_trace}`. |
| `GET` / `DELETE` | `/chat-history/{customer_id}` | List a customer's sessions/messages; delete all but `keep_session_id`. |
| `GET` | `/health` | Redis + DB liveness. |
| `POST` | `/update-settings` | Hot-swaps the active LLM provider/temperature/max_tokens in `app.state.llm`. |

### Prompts

Prompts live as YAML in `prompts/*.yml` with a single `prompt:` key. [src/utils/file_utils.py:read_prompt_file](src/utils/file_utils.py) loads each into a `langchain_core.prompts.PromptTemplate` at startup; the bundle is held in `Prompts` (see `app_models.py`). The codebase passes `prompt.template` (the raw string) to the LLM as the system message and builds the user message with f-strings — it does **not** use `PromptTemplate.format`. Filenames-to-fields are wired in [src/app_setting.py](src/app_setting.py); add a new prompt by extending `AppSettings`, `Prompts`, and `get_all_prompts`.

### State, persistence, and external services

- **SQLite** `transactions.db` at the **repo root** (`AppSettings.db_path`, resolved against the project root) holds the `transactions` table plus app tables created at startup by `db_utils.setup_chat_tables`: `chat_sessions`, `chat_messages`, `request_metrics`, `query_cache`. One connection is opened at startup and shared via `app.state`.
- **Redis** (`REDIS_URL`, default `localhost:6379`) stores per-session chat history under `chat:{customer_id}:{session_id}` (TTL `REDIS_TTL`) and the SQL cache. Optional — the app degrades gracefully without it.
- **Chroma** `help_store/` (collection `barclays_help`) is the help-centre knowledge base; it is opened in `lifespan` and, if it fails to open, the help branch returns the grounded fallback reply.
- **FastAPI DI** — [src/dependencies.py](src/dependencies.py) provides `get_llm`, `get_prompts`, `get_logging`, `get_redis_client`, `get_db_conn` as request-scoped dependencies pulling from `app.state`.

### Database schema (the only table the SQL agent should target)

`transactions(transaction_id PK, customer_id, card_number, transaction_date, year, month, day, amount_usd, transaction_type, balance, merchant_brand_name, merchant_system_category1_name, merchant_system_category2_name)`. `transaction_type` is one of `income`, `payment`, `direct_debit`, `standing_order`; `amount_usd` is always positive (direction comes from `transaction_type`, not sign); `balance` is the running account balance after each transaction. The table is built by [src/clients/sqllite_db_creation.py](src/clients/sqllite_db_creation.py) from `data/data_processing/df_transaction_data_enhanced.xlsx`, which is produced by [data/data_processing/transaction_data_enhance.py](data/data_processing/transaction_data_enhance.py) (`build` reshapes the data; `shift` re-bases dates so the latest transaction is today). The supervisor prompt ([prompts/suprivisor_prompt.yml](prompts/suprivisor_prompt.yml)) and the text2sql prompt ([prompts/text2sql_generator.yml](prompts/text2sql_generator.yml)) encode this schema; keep them in sync if the schema changes.

### Data layout

- `data/data_processing/` — raw `df_transaction_data.xlsx` → `df_transaction_data_preprocessed.xlsx` (via `data/transaction_data_preprocess.py`) → `df_transaction_data_enhanced.xlsx` (via `transaction_data_enhance.py build`) → `transactions.db`.
- `help_chunks.xlsx` — inspection dump of the chunks embedded into `help_store/`.
- `tests/data/` — curated evaluation datasets (golden set, context-builder eval sets).

## Conventions and gotchas

- **The misspelling `suprivisor` (instead of `supervisor`) is load-bearing.** It appears in the prompt filename, the `Prompts.suprivisor_prompt` field, the agent module/function names, and the `Graph_State.suprivisor_response` field. Don't "fix" it as part of an unrelated change.
- `Agent_Graph.__init__` sets `self.logger = logger` **after** `self.app = self._workflow()`, but `_workflow` doesn't use the logger so this works. Don't reorder so a node method that needs the logger gets called during compile.
- `SubGraph_State.agent_metrics` is expected to already be a dict when the subgraph starts. In the normal flow the `guardrail` agent initializes `agent_metrics = {}`. If you add a graph path that reaches the subgraph without going through `guardrail`, initialize it yourself.
- `_supervisor_router` picks a single primary route (BankSQL first, else help, else fallback); `_after_sql_router` chains help_center after the SQL branch so the two never write the shared `agent_metrics` channel concurrently. The `join` node synchronises before `response_generator`.
- Prompt-cache hints: agents send the system prompt with `cache_control` (ephemeral, 1h). Edit prompt text freely, but expect the first call afterwards to be a cache miss.
- The LLM client (`clients/llm_client.py`) supports `openai`, `anthropic`, `gemini`, `groq`; each reads its own API key from the environment. Embeddings always use OpenAI regardless of the chat provider.
