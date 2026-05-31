# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A FastAPI + LangGraph backend for a **banking-transactions chatbot**. It accepts a natural-language user query, runs it through security guardrails, classifies/decomposes it, generates SQL against a single SQLite `transactions` table, executes the query, optionally produces a Plotly chart, and rephrases the result into a chat reply. The repo was migrated from a food-ordering chatbot to a banking one; some stale food/restaurant references still exist in comments, in `data/schema_notes.md`, in `utils/agent_utils.py` (`FIELD_TO_INDEX_KEY`), and in the test-data-generation scenarios — treat those as legacy unless explicitly being reused.

The default LLM provider is **Anthropic** (`claude-sonnet-4-6`), set by `LLM_PROVIDER` in [src/app_setting.py](src/app_setting.py). The provider is hot-swappable at runtime via `POST /update-settings` — supported values are `"openai"`, `"anthropic"`, `"gemini"`, `"groq"`. `text-embedding-3-small` (OpenAI) is the embedding model used for non-runtime utilities.

## Common commands

All commands are run from the repo root unless noted. The server must be run with `src/` on the import path — `python src/app.py` works because Python prepends the script's directory to `sys.path`.

```powershell
# Install dependencies (Python 3.11+)
pip install -r requirements.txt

# Start a local Redis (required — chat history is cached at localhost:6379)
# Then run the FastAPI app on 0.0.0.0:8000 with reload on src/ and prompts/
python src/app.py

# Streamlit wrapper that embeds the frontend in an iframe (optional UI shell)
streamlit run src/dashboard.py

# Interactive Rich-CLI client (talks to http://127.0.0.1:8000/process)
python src/simulator.py

# (Re)build the SQLite DB from the preprocessed Excel.
# Backs up any existing transactions.db with a timestamped suffix.
python src/clients/sqllite_db_creation.py --db transactions.db `
    --xlsx data/data_processing/df_transaction_data_preprocessed.xlsx

# Print the schema of the configured DB (transactions.db)
python src/misc/get_db_info.py

# Dump every table in a SQLite DB to per-table .xlsx files
python src/misc/export_db_tables.py --db transactions.db --out-dir exported_excels

# Generate synthetic eval data for the context_builder agent
python tests/test_data_generation.py

# Batch-evaluate the full graph against tests/data/golden_data_set_ver1.xlsx.
# Requires the server to be running. Writes debug_results.xlsx with two sheets:
# `summary` (graph-state per row) and `agent_metrics` (per-agent latency / cost / tokens).
python tests/test_process_debug.py
```

`.env` at the repo root must contain the API key for whichever provider is active (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`). The file is gitignored and read by `clients/llm_client.py` via `python-dotenv`.

There is no test framework wired up — `tests/test_*.py` files are integration drivers (HTTP clients, eval harnesses, dataset generators), **not** pytest suites. Don't try to run them with `pytest`.

## Architecture

### Request flow

`POST /process` constructs a `Graph_State` and invokes a compiled LangGraph workflow. The eval-only `POST /process-debug` skips Redis and accepts `chat_history` directly in the request body — it also activates `_local_transaction_fallback` when the graph returns a technical error message. There are **two** graphs:

- **Outer graph** ([src/agent_graph.py](src/agent_graph.py)) — `guardrail → {context_builder | fallback} → supervisor → {sql_generation subgraph | fallback | END} → join → visualization_agent → response_generator → END`. Routing is in `_guardrail_router`, `_context_builder_router`, and `_supervisor_router`.
- **Inner subgraph** ([src/agent_subraph.py](src/agent_subraph.py)) — `sql_generation → sql_execution → {END | sql_correction → sql_execution | fallback}`. `sql_correction` is invoked at most once: the `_sql_execution_router` checks `state.sql_query_correction` to avoid an infinite loop.

The outer graph **adds the subgraph as a node** (`builder.add_node("sql_generation", self.agent_subgraph.app)`), so both states must remain field-compatible where they overlap. `Graph_State` and `SubGraph_State` are both defined in [src/app_models.py](src/app_models.py); changes to fields the subgraph reads/writes (e.g. `text2sql_generator_response`, `database_query_results`, `sql_query_correction`, `banksql_queries`, `agent_metrics`) must be made in both models.

### Agents

Each agent is a stand-alone async function in `src/agents/` that takes `(state, llm, prompt, logger)`, mutates the state, and returns it. The graph classes are thin wrappers — node methods just delegate. Conventions every agent follows:

- LLM calls use `llm.with_structured_output(<PydanticModel>, include_raw=True)` so the response is both parsed and the raw `AIMessage` is available for metrics.
- Each invocation passes `prompt_cache_key="<agent>-v2"` — bump the suffix when the prompt content changes so the provider doesn't serve a stale cached prompt.
- Metrics are computed by `utils.agent_utils.agent_metrics()` (token counts, latency, cost) and stored under `state.agent_metrics["<agent_name>"]`.
- The whole body is wrapped in `try / except` that sets `state.fallback_response = FallbackResponse(fallback_reason="exception", fallback_node=<name>, ...)`. Routers then steer the flow to the fallback node. **The fallback agent is the only thing that produces a user-visible error message.**

**Agent inventory:**

| Agent | File | Role |
|---|---|---|
| `guardrail` | [src/agents/guardrails.py](src/agents/guardrails.py) | First node. LLM-based input security check (SYSTEM_VIOLATION, SQL_VIOLATION, PII_VIOLATION). Prompt is hardcoded in the function, no YAML prompt used. Sets `fallback_response` on violation. Also initializes `agent_metrics = {}` before context_builder runs. |
| `context_builder` | [src/agents/context_builder.py](src/agents/context_builder.py) | Extracts structured context from the user query and chat history. |
| `supervisor` | [src/agents/supervisor.py](src/agents/supervisor.py) | Classifies query into `banksql_queries` or `out_of_scope_queries`. |
| `text2sql_generator` | [src/agents/text2sql_generator.py](src/agents/text2sql_generator.py) | Converts natural language to SQL. |
| `sql_execution` | [src/agents/sql_execution.py](src/agents/sql_execution.py) | Runs the SQL against SQLite. |
| `sql_correction` | [src/agents/sql_correction.py](src/agents/sql_correction.py) | Attempts to fix a failed SQL query (runs at most once). |
| `visualization_agent` | [src/agents/visualization_agent.py](src/agents/visualization_agent.py) | Decides whether to render a chart; builds Plotly JSON (bar/line) from SQL results. Skips if fallback is active or no data. Stores result in `state.visualization_response`. |
| `response_generator` | [src/agents/response_generator.py](src/agents/response_generator.py) | Rephrases SQL results into a natural-language reply. |
| `fallback` | [src/agents/fallback.py](src/agents/fallback.py) | Converts `state.fallback_response` into a user-visible error message. Only node that surfaces errors to the user. |

### API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves `src/index.html` (the chat UI). |
| `POST` | `/login` | Validates `user_id` + `password` against env vars `DASHBOARD_USER_IDS` / `DASHBOARD_PASSWORD`. |
| `POST` | `/process` | Main chat endpoint. Reads/writes Redis chat history. Returns `{response, visualization}`. |
| `POST` | `/process-debug` | Eval-only. Accepts `chat_history` directly; returns full graph state. Activates local SQL fallback on LLM error. |
| `POST` | `/process-voice` | Accepts audio upload, transcribes with Whisper (`whisper-1`), then routes through `/process`. |
| `POST` | `/update-settings` | Hot-swaps the active LLM provider/model in `app.state.llm` without restarting. |

### Prompts

Prompts live as YAML in `prompts/*.yml` with a single `prompt:` key. [src/utils/file_utils.py:read_prompt_file](src/utils/file_utils.py#L15) loads each one into a `langchain_core.prompts.PromptTemplate` at startup; the bundle is held in `Prompts` (see `app_models.py`). Note that the codebase passes `prompt.template` (the raw string) to the LLM as the system message and constructs the user message manually with f-strings — it does **not** use `PromptTemplate.format`. Filenames-to-fields are wired in [src/app_setting.py](src/app_setting.py); add a new prompt by extending `AppSettings`, `Prompts`, and `get_all_prompts`. The `guardrail` agent is the exception — its prompt is hardcoded directly in the function.

### State, persistence, and external services

- **SQLite** (`transactions.db`) is opened once at app startup and the connection is reused across requests via FastAPI's `app.state`. DB path is `AppSettings.db_path` (default `"transactions.db"`, resolved relative to the project root). The active DB is `src/transactions.db`.
- **Redis** at `localhost:6379` stores per-session chat history under key `chat:{customer_id}:{session_id}` with a 300-second TTL ([src/utils/file_utils.py:42](src/utils/file_utils.py#L42)). If Redis isn't running, app startup fails.
- The `/process-debug` endpoint skips Redis and accepts `chat_history` directly in the request body — that's how the batch evaluator drives the graph deterministically.
- **FastAPI DI** — [src/dependencies.py](src/dependencies.py) provides `get_llm`, `get_prompts`, `get_logging`, `get_redis_client`, `get_db_conn`, `get_user_data`, `get_llm_embeddings` as request-scoped dependencies pulling from `app.state`.

### Database schema (the only table the SQL agent should target)

`transactions(transaction_id PK, customer_id, card_number, country, card_type, card_present, channel, transaction_date, year, month, day, amount_usd, merchant_brand_name, merchant_system_category1_name, merchant_system_category2_name)`. The supervisor prompt at [prompts/suprivisor_prompt.yml](prompts/suprivisor_prompt.yml) encodes this schema as the definition of the "BankSQL" class; keep it in sync if the schema changes. `data/schema_notes.md` is **stale** (it documents the old food-ordering schema).

## Conventions and gotchas

- **The misspelling `suprivisor` (instead of `supervisor`) is load-bearing.** It appears in the prompt filename, the `Prompts.suprivisor_prompt` field, the agent module/function names, and the `Graph_State.suprivisor_response` field. Don't "fix" it as part of an unrelated change — it would touch ~10 files.
- `Agent_Graph.__init__` sets `self.logger = logger` **after** `self.app = self._workflow()`, but `_workflow` doesn't use the logger so this works. Don't reorder so a node method that needs the logger gets called during compile.
- `SubGraph_State.agent_metrics` is expected to already be a dict when the subgraph starts. In the normal flow the `guardrail` agent initializes `agent_metrics = {}`. If you add a graph path that reaches the subgraph without going through `guardrail`, initialize `agent_metrics = {}` yourself.
- `_supervisor_router` can return **multiple routes** simultaneously (e.g. both `sql_generation` and `fallback` when there are multiple banksql queries). LangGraph fans these out; the `join` node synchronises them before `visualization_agent`.
- `transactions.db` is checked into the repo (~880KB). `orders.db` (~13MB) is the legacy food-ordering DB and is unused by the current code path; leave it alone unless intentionally removing legacy assets.
- The `data/embeddings_data/` FAISS indices and `chroma_store/` are leftovers from the food-ordering version. The current banking flow does not load any vector store at runtime — `get_vectordb_data` in `file_utils.py` exists but is not called from `lifespan`.
- The LLM client (`clients/llm_client.py`) supports four providers: `openai`, `anthropic`, `gemini`, `groq`. The active provider is determined by `AppSettings.LLM_PROVIDER` (default `"anthropic"`). Each provider reads its own API key from the environment. Embeddings always use OpenAI regardless of the chat provider.
- `dashboard.py` is a minimal Streamlit app that renders `src/index.html` inside a full-screen iframe — it is just a UI shell and does not contain application logic.
