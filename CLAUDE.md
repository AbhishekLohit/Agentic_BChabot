# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A FastAPI + LangGraph backend for a **banking-transactions chatbot**. It accepts a natural-language user query, classifies/decomposes it, generates SQL against a single SQLite `transactions` table, executes the query, and rephrases the result into a chat reply. The repo was recently migrated from a food-ordering chatbot to a banking one (see commit `Migration from food to banking chatbot`); some stale food/restaurant references still exist in comments, in `data/schema_notes.md`, in `utils/agent_utils.py` (`FIELD_TO_INDEX_KEY`), and in the test-data-generation scenarios — treat those as legacy unless explicitly being reused.

OpenAI's `gpt-4.1-mini` is the default LLM (configured in [src/app.py:41](src/app.py#L41)); `text-embedding-3-small` is the embedding model.

## Common commands

All commands are run from the repo root unless noted. The server must be run with `src/` on the import path — `python src/app.py` works because Python prepends the script's directory to `sys.path`.

```powershell
# Install dependencies (Python 3.11+)
pip install -r requirements.txt

# Start a local Redis (required — chat history is cached at localhost:6379)
# Then run the FastAPI app on 0.0.0.0:8000 with reload on src/ and prompts/
python src/app.py

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

`.env` at the repo root is loaded by `clients/openai_client.py` via `python-dotenv` and must contain `OPENAI_API_KEY`. The file is gitignored.

There is no test framework wired up — `tests/test_*.py` files are integration drivers (HTTP clients, eval harnesses, dataset generators), **not** pytest suites. Don't try to run them with `pytest`.

## Architecture

### Request flow

`POST /process` (and the eval-only `POST /process-debug` which returns the full graph state) constructs a `Graph_State` and invokes a compiled LangGraph workflow. There are **two** graphs:

- **Outer graph** ([src/agent_graph.py](src/agent_graph.py)) — `context_builder → supervisor → {sql_generation subgraph | fallback} → join → response_generator → END`. Routing is in `_context_builder_router` and `_supervisor_router`.
- **Inner subgraph** ([src/agent_subraph.py](src/agent_subraph.py)) — `sql_generation → sql_execution → {END | sql_correction → sql_execution | fallback}`. `sql_correction` is invoked at most once: the `_sql_execution_router` checks `state.sql_query_correction` to avoid an infinite loop.

The outer graph **adds the subgraph as a node** (`builder.add_node("sql_generation", self.agent_subgraph.app)`), so both states must remain field-compatible where they overlap. `Graph_State` and `SubGraph_State` are both defined in [src/app_models.py](src/app_models.py); changes to fields the subgraph reads/writes (e.g. `text2sql_generator_response`, `database_query_results`, `sql_query_correction`, `banksql_queries`, `agent_metrics`) must be made in both models.

### Agents

Each agent is a stand-alone async function in `src/agents/` that takes `(state, llm, prompt, logger)`, mutates the state, and returns it. The graph classes are thin wrappers — node methods just delegate. Conventions every agent follows:

- LLM calls use `llm.with_structured_output(<PydanticModel>, include_raw=True)` so the response is both parsed and the raw `AIMessage` is available for metrics.
- Each invocation passes `prompt_cache_key="<agent>-v2"` — bump the suffix when the prompt content changes so OpenAI doesn't serve a stale cached prompt.
- Metrics are computed by `utils.agent_utils.agent_metrics()` (token counts, latency, cost with/without cache using hard-coded GPT-4.1-mini prices) and stored under `state.agent_metrics["<agent_name>"]`.
- The whole body is wrapped in `try / except` that sets `state.fallback_response = FallbackResponse(fallback_reason="exception", fallback_node=<name>, ...)`. Routers then steer the flow to the fallback node. **The fallback agent is the only thing that produces a user-visible error message.**

### Prompts

Prompts live as YAML in `prompts/*.yml` with a single `prompt:` key. [src/utils/file_utils.py:read_prompt_file](src/utils/file_utils.py#L15) loads each one into a `langchain_core.prompts.PromptTemplate` at startup; the bundle is held in `Prompts` (see `app_models.py`). Note that the codebase passes `prompt.template` (the raw string) to the LLM as the system message and constructs the user message manually with f-strings — it does **not** use `PromptTemplate.format`. Filenames-to-fields are wired in [src/app_setting.py](src/app_setting.py); add a new prompt by extending `AppSettings`, `Prompts`, and `get_all_prompts`.

### State, persistence, and external services

- **SQLite** (`transactions.db`) is opened once at app startup and the connection is reused across requests via FastAPI's `app.state`. The connection is sqlite3 default (not thread-safe in general but fine for the single-threaded reload server). DB path is `AppSettings.db_path` (default `"transactions.db"`, resolved from cwd).
- **Redis** at `localhost:6379` stores per-session chat history under key `chat:{customer_id}:{session_id}` with a 300-second TTL ([src/utils/file_utils.py:42](src/utils/file_utils.py#L42)). If Redis isn't running, app startup fails.
- The `/process-debug` endpoint skips Redis and accepts `chat_history` directly in the request body — that's how the batch evaluator drives the graph deterministically.

### Database schema (the only table the SQL agent should target)

`transactions(transaction_id PK, customer_id, card_number, country, card_type, card_present, channel, transaction_date, year, month, day, amount_usd, merchant_brand_name, merchant_system_category1_name, merchant_system_category2_name)`. The supervisor prompt at [prompts/suprivisor_prompt.yml](prompts/suprivisor_prompt.yml) encodes this schema as the definition of the "BankSQL" class; keep it in sync if the schema changes. `data/schema_notes.md` is **stale** (it documents the old food-ordering schema).

## Conventions and gotchas

- **The misspelling `suprivisor` (instead of `supervisor`) is load-bearing.** It appears in the prompt filename, the `Prompts.suprivisor_prompt` field, the agent module/function names, and the `Graph_State.suprivisor_response` field. Don't "fix" it as part of an unrelated change — it would touch ~10 files.
- `Agent_Graph.__init__` sets `self.logger = logger` **after** `self.app = self._workflow()`, but `_workflow` doesn't use the logger so this works. Don't reorder so a node method that needs the logger gets called during compile.
- `SubGraph_State.agent_metrics` is expected to already be a dict when the subgraph starts — the outer `context_builder` agent initializes it. If you add a graph path that reaches the subgraph without going through `context_builder`, initialize `agent_metrics = {}` yourself.
- `transactions.db` is checked into the repo (~880KB). `orders.db` (~13MB) is the legacy food-ordering DB and is unused by the current code path; leave it alone unless intentionally removing legacy assets.
- The `data/embeddings_data/` FAISS indices and `chroma_store/` are leftovers from the food-ordering version. The current banking flow does not load any vector store at runtime — `get_vectordb_data` in `file_utils.py` exists but is not called from `lifespan`.
- The OpenAI client (`clients/openai_client.py`) reads `OPENAI_API_KEY` from the environment via `dotenv.load_dotenv()` at import time. There is no per-request key.
