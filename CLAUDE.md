# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A FastAPI + LangGraph backend for a **banking-transactions chatbot** (Barclays-style "FinTell"). It accepts a natural-language user query, runs it through a security guardrail, splits/classifies it, then either (a) generates SQL against a single SQLite `transactions` table, executes it, and optionally builds a Plotly chart, (b) answers a how-to question by RAG over the bank-neutral help documents in `data/help_documents/`, or (c) helps the customer raise a **non-fraud card dispute** (collects the payment, reason and answers, gets an explicit confirmation, then raises a case). The result is rephrased into a chat reply.

The default LLM provider is **Anthropic** (`claude-sonnet-4-6`), set by `LLM_PROVIDER` in [src/app_setting.py](src/app_setting.py). The provider is hot-swappable at runtime via `POST /update-settings` — supported values are `"openai"`, `"qwen"`, `"anthropic"`, `"gemini"`, `"groq"`, `"ollama"` (local, no key) and `"capstone"` (the IIT KGP Qwen3-8B API, `CAPSTONE_*` settings). Qwen uses the OpenAI-compatible client with `QWEN_API_KEY`, `QWEN_API_BASE_URL`, `QWEN_MODEL`, and `QWEN_CA_CERT`. The project is being tuned for **Qwen3-8B** (8K context window), which is why prompts and `HELP_TOP_K` are kept small.

Embeddings are chosen separately by `EMBEDDING_PROVIDER` (`get_embeddings` in `clients/llm_client.py`): `local` (MiniLM, offline, the default), `capstone` (bge-small-en-v1.5 API, needs `CAPSTONE_EMBED_API_KEY` and `data/certs/vm02-ca.crt`) or `openai`. The committed help index was built with **capstone bge-small**, so query-time embeddings must use the same provider or retrieval is meaningless.

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

# (Re)build the help-document Chroma index (data/help_documents -> chroma_store/help_documents).
# The index is committed; rebuild only after changing the documents, chunking or embedding model,
# then commit it with `git add -A chroma_store`. Running the app can touch chroma.sqlite3 —
# `git restore chroma_store` if a pull complains; don't commit it otherwise.
cd src; python -m utils.embeddings.help_document_store; cd ..

# Data pipeline (raw -> preprocessed -> enhanced). `shift` re-bases dates so the latest txn is today.
python data/data_processing/transaction_data_enhance.py build
python data/data_processing/transaction_data_enhance.py shift
```

Tests (no API keys needed — they use a scripted fake chat model in `tests/fakes.py`):

```powershell
python -m pytest tests -q
# single file / single test
python -m pytest tests/test_dispute_workflow.py -q
python -m pytest tests/test_dispute_graph.py -k "<name substring>" -q
```

`tests/conftest.py` puts `src/` on `sys.path` and builds an in-memory `transactions` table with a fixed `TODAY`. The tests cover the Disputes checks and workflow, graph routing around disputes, the visualization agent, and the offline parts of the evaluation harnesses (golden dataset, context-builder eval, prompt baseline); none of them call a real model.

### Evaluation harnesses (live model calls; need `.env` as for the app)

- **End-to-end (the primary evaluation)**: [evaluation/e2e/](evaluation/e2e/README.md). 80 cases in `golden_cases.yaml`, built answer-first from `transactions.db` and the help documents, with gold for every stage (guardrail, gate, context, routes, sub-agents, SQL result, RAG evidence, dispute decision, reply). `validate.py` checks the gold against the raw data (no model calls). `run.py` drives the real graph: one fresh DB copy per case, and dispute dialogues answered by a rule-based simulated customer. `score.py` reports accuracy, task completion, sub-agent selection, calls, cost and latency, and a first-failing-stage taxonomy (retrieval miss / reasoning / tool-call / hallucination), with an optional cached Claude judge (`--judge`). Offline tests: `tests/test_e2e_scoring.py`.
- `evaluation/evaluation_cases.jsonl` is the shared, suite-tagged golden dataset (`suite: "sbi_rag"`, `"context_builder"`, …) and the source of truth; `rag_eval_set.csv` is generated from it.
- **Context builder**: `python -m evaluation.context_builder_run --validate-only` (no calls), then `--live --limit 3 --as-of 2026-11-22`. See [evaluation/CONTEXT_BUILDER_EVALUATION.md](evaluation/CONTEXT_BUILDER_EVALUATION.md).
- **Help RAG**: [evaluation/rag/README.md](evaluation/rag/README.md) (`evaluate.py`, `validate_eval_set.py --freeze`). Retrieval and answers are scored separately; evidence is matched by verbatim quote rather than chunk ID, so one eval set works across chunking experiments. Chunking, retrieval-mode and ablation experiments are still under the older `eval/rag/` folder and import `utils.help_rag`, so they measure exactly what the app runs.
- **Week-1 prompt baseline** (single-prompt router + SQL, 5 strategies): `python evaluation/prompt_baseline/run.py --validate-only` / `--limit 5`. See [evaluation/prompt_baseline/README.md](evaluation/prompt_baseline/README.md).
- Results go to git-ignored `results/` folders.

## Architecture

### Request flow

`POST /process` and `POST /process-stream` share `_prepare_request()` in [src/app.py](src/app.py), which loads chat history (Redis, seeded from the last 24h of SQLite messages for a fresh conversation, summarised once there are ≥5 prior user turns), builds a `Graph_State`, and compiles the graphs per request. There are **two** graphs:

- **Outer graph** ([src/agent_graph.py](src/agent_graph.py)) — `guardrail → {dispute_gate | fallback}`; `dispute_gate → {context_builder | dispute_agent | fallback}`; `context_builder → {suprivisor | fallback | END}`; `suprivisor → {dispute_agent | sql_generation subgraph | help_center | fallback | END}`; `dispute_agent → {context_builder | sql_generation | help_center | join}`; then `(help_center if the message also had a help query) → join → visualization_agent → response_generator → END`. Routing is in `_guardrail_router`, `_dispute_gate_router`, `_context_builder_router`, `_supervisor_router`, `_after_dispute_router` and `_after_sql_router`.
- **Inner subgraph** ([src/agent_subraph.py](src/agent_subraph.py)) — `sql_generation → sql_execution → {END | sql_correction → sql_execution | fallback}`. `sql_correction` runs at most once: `_sql_execution_router` checks `state.sql_query_correction` to avoid an infinite loop.

[src/graph_runner.py](src/graph_runner.py) holds `build_agent_graph`, `build_graph_state`, `run_turn` and the UI trace builder (`_build_agent_trace`, re-exported from `app.py`). `/process-debug` and the e2e evaluation runner both use it, so they drive the same graph. `GRAPH_CONFIG` (the `AGENT_RECURSION_LIMIT` step cap) lives there too.

The outer graph **adds the subgraph as a node** (`builder.add_node("sql_generation", self.agent_subgraph.app)`), so both states must remain field-compatible where they overlap. `Graph_State` and `SubGraph_State` are both defined in [src/app_models.py](src/app_models.py); changes to fields the subgraph reads/writes (e.g. `text2sql_generator_response`, `database_query_results`, `sql_query_correction`, `banksql_queries`, `agent_metrics`) must be made in both models.

### Disputes ([src/agents/disputes/](src/agents/disputes))

The Disputes agent helps a customer raise a **non-fraud card dispute**. It is a fixed workflow, not a free-roaming agent: per turn the model makes ONE structured call (`DisputeTurnExtraction`: amount, date, reason, slot answers, intent) and code does the rest — `amount + date → find the payment in the database → customer confirms merchant, amount and date → confirm the reason → pre-checks → ask the reason's questions → summary + explicit confirmation → raise the case`.

- **Where it runs.** The supervisor has a fourth class, `Dispute` (a customer reporting a problem with a payment; "how do I dispute a transaction?" stays `HelpCenter`). `dispute_agent` runs at most once per request (`dispute_handled`), so the `dispute_agent → context_builder` cycle cannot loop; a second dispute classification in the same request is treated as out of scope.
- **Finding the payment.** The customer must give the **date, the merchant and the amount**; whichever are missing are asked for, and nothing is searched until all three are known. `checks.find_transactions` looks the payment up with a fixed, parameterised query on the signed-in customer's own rows: the date exactly, the amount within `DISPUTE_AMOUNT_TOLERANCE` (±£3), and the merchant fuzzily (`checks.merchant_score`: case/punctuation ignored, part of the name or initials count as a full match, typos scored by similarity, must reach `DISPUTE_MERCHANT_MATCH`). Results are ordered by amount difference, then merchant score. There is no model-written SQL on this path. All transaction types are searched so a direct debit gets a clear "can't be disputed here" answer. The model's only part is reading the details out of the message (it is given today's date so "13 June" and "yesterday" work); code rejects non-positive amounts and unreal or future dates.
- **Confirming the payment.** A single **exact match** (`checks.is_exact_match`: amount to the penny, merchant fully matching) is shown back with its **merchant, amount and date** (read from the database, never from the customer's words) and needs an explicit yes (`proposed_transaction_id` in the draft; the yes must arrive on a later turn). Otherwise up to `DISPUTE_MAX_CHOICES` (3) close payments are listed and the customer picks one, which can only be one of the ids that were listed, and that pick counts as the confirmation. "No"/"none of those" clears the details and asks again; a new date, merchant or amount searches again. Nothing else in the dispute proceeds until a payment is confirmed. Eligibility (card payment, inside the window, not already disputed) is explained as soon as a payment is found, before asking for the yes. Full write-up: [docs/disputes_agent.md](docs/disputes_agent.md).
- **Continuity.** The in-progress dispute is a draft row in SQLite (`dispute_drafts`, per customer + session, ignored after `DISPUTE_DRAFT_MAX_AGE_HOURS`). It is in SQLite, not Redis, because Redis is optional. `dispute_gate` runs right after the guardrail on every request; with no draft open it does nothing. With one open, a single model call reads the message and sets `message_kind`: `dispute` (continue), `side_question` (answer it through the normal pipeline and append a reminder of what the dispute is waiting for), `both` (continue the dispute and run the unrelated part, `pipeline_query`, through the normal pipeline) or `new_dispute` (drop the draft, start fresh). The gate's reading is handed to `dispute_agent` so the message is read once.
- **Human in the loop.** The customer confirms twice: the payment (merchant, amount, date), then the final summary. **Confirmation gate:** a "yes" raises the case only if the summary was persisted as `awaiting_confirmation` on an *earlier* turn, the message changes no details, it does not also carry another request (`allow_confirm`), and the draft fingerprint still matches what was shown. Nothing else writes a case. A payment the customer does not recognise ends as `suspected_fraud` (recorded in `dispute_events`, handed to the fraud team) and never files a dispute.
- **Wording.** The agent returns a `DisputeResult` (status + facts); `response_generator` phrases it with [prompts/dispute_reply_prompt.yml](prompts/dispute_reply_prompt.yml) (tokens stream as usual) and falls back to the fixed text in `plain_reply` if that call fails. The case ID is always appended if the model leaves it out.
- **Config.** [prompts/dispute_types.yml](prompts/dispute_types.yml) holds the question set per reason (adding or changing a reason is a config edit; ids must match `DisputeReason` in `app_models.py`). [checks.py](src/agents/disputes/checks.py) holds the fixed SQL: eligibility (card `payment` only, inside `DISPUTE_WINDOW_DAYS` of **real today**, not already disputed), duplicate pair, recurring pattern, refund credit. The bundled `transactions.db` runs from 2023-05-16 to 2026-11-21, so recent rows fall inside the default 120-day window. As real time moves on, widen `DISPUTE_WINDOW_DAYS` or run the `shift` data script.
- **Tables** (created by `setup_chat_tables`): `dispute_drafts`, `disputes` (unique open case per customer + payment + reason), `dispute_events` (audit trail).

### Agents

Each agent is a stand-alone async function in `src/agents/` that takes `(state, llm, prompt, logger)` (plus extras where noted), mutates the state, and returns it. The graph classes are thin wrappers. Conventions:

- LLM calls use `llm.with_structured_output(<PydanticModel>, include_raw=True)` so the response is both parsed and the raw `AIMessage` is available for metrics. (`response_generator` and `help_center` stream plain text instead so `/process-stream` can surface tokens.)
- Metrics are computed by `utils.agent_utils.agent_metrics()` (token counts, latency, cost) and stored under `state.agent_metrics["<agent_name>"]`. Cost is priced by the model that answered (`MODEL_PRICES`, USD per 1M tokens). Self-hosted Qwen/capstone uses `CAPSTONE_COST_PER_M_IN/OUT` (default 0), and an unknown model costs 0, so add new models to the table.
- SQL execution (`sql_query`) and the dispute lookups (`dispute_find_transactions`, `dispute_check_eligibility`, `dispute_create_case`) are wrapped in `langsmith.traceable(run_type="tool")`: tool spans when tracing is on, plain calls when it is off.
- The whole body is wrapped in `try / except` that sets `state.fallback_response = FallbackResponse(fallback_reason="exception", fallback_node=<name>, ...)`. Routers then steer the flow to the fallback node.

| Agent | File | Role |
|---|---|---|
| `guardrail` | [src/agents/guardrails.py](src/agents/guardrails.py) | First node. LLM-based input check (SYSTEM / SQL / PII violation, or GREETING). Prompt is hardcoded in the function — no YAML. Initializes `agent_metrics = {}`. |
| `context_builder` | [src/agents/context_builder.py](src/agents/context_builder.py) | Splits the query + chat history into one or more self-contained `contextual_queries`. |
| `dispute_gate` / `dispute_agent` | [src/agent_graph.py](src/agent_graph.py), [src/agents/disputes/workflow.py](src/agents/disputes/workflow.py) | See "Disputes". Graph nodes, not stand-alone agent functions. |
| `supervisor` | [src/agents/supervisor.py](src/agents/supervisor.py) | Classifies each sub-query into `banksql_queries`, `help_center_queries`, `dispute_queries` or `out_of_scope_queries`. |
| `text2sql_generator` | [src/agents/text2sql_generator.py](src/agents/text2sql_generator.py) | NL → SQL, with a Redis + SQLite (`query_cache`) cache keyed on customer + query. |
| `sql_execution` | [src/agents/sql_execution.py](src/agents/sql_execution.py) | Runs the SQL; auto-adds/clamps `LIMIT` to `MAX_RESULT_ROWS` (20). Model-written SQL is **read-only**: a SQLite authorizer (`_read_only`, scoped to the calling thread) allows only reads of `READABLE_TABLES` (`transactions`), so writes, other tables, `ATTACH` and `PRAGMA` fail as a normal SQL error (→ `sql_correction` → fallback). |
| `sql_correction` | [src/agents/sql_correction.py](src/agents/sql_correction.py) | Repairs a failed SQL query (runs at most once). |
| `help_center` (node) → `help_agent` | [src/agents/help_agent.py](src/agents/help_agent.py) | RAG answer from `data/help_documents` via [src/utils/help_rag.py](src/utils/help_rag.py). Retrieval mode is `HELP_RETRIEVAL_MODE` (`dense` / `bm25` / `hybrid` RRF / `hybrid_rerank` with flashrank); excerpts are numbered `[n]` and **citations are enforced in code** after generation (`HELP_CITATION_POLICY`: `strict` keeps an uncited sentence only if one excerpt contains at least 60% of its content words, adding that citation (`supporting_excerpt`); every other uncited sentence is dropped). `help_rag` is shared with the eval scripts, so change retrieval there, not in the agent. |
| `visualization_agent` | [src/agents/visualization_agent.py](src/agents/visualization_agent.py) | Decides whether to chart; builds Plotly JSON from SQL results. Skipped on fallback / no data. |
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
| `POST` | `/process-debug` | Same pipeline, but returns the full graph state (routing, `help_source`, citation report, SQL, metrics). For debugging and evaluation. |
| `POST` | `/process-voice` | Multipart audio upload → OpenAI Whisper transcription → normal pipeline. Returns 503 without `OPENAI_API_KEY` (text chat is unaffected). |
| `GET` / `DELETE` | `/chat-history/{customer_id}` | List a customer's sessions/messages; delete all but `keep_session_id`. |
| `GET` | `/health` | Redis + DB liveness. |
| `POST` | `/update-settings` | Hot-swaps the active LLM provider/temperature/max_tokens in `app.state.llm`. |

### Prompts

Prompts live as YAML in `prompts/*.yml` with a single `prompt:` key. [src/utils/file_utils.py:read_prompt_file](src/utils/file_utils.py) loads each into a `langchain_core.prompts.PromptTemplate` at startup; the bundle is held in `Prompts` (see `app_models.py`). The codebase passes `prompt.template` (the raw string) to the LLM as the system message and builds the user message with f-strings — it does **not** use `PromptTemplate.format`. Filenames-to-fields are wired in [src/app_setting.py](src/app_setting.py); add a new prompt by extending `AppSettings`, `Prompts`, and `get_all_prompts`. `prompts/dispute_types.yml` is configuration, not a prompt (no `prompt:` key); it is loaded separately by `agents/disputes/registry.py`, and its questions use `{merchant}`, `{amount}` and `{date}` placeholders that the workflow fills from the transaction.

### State, persistence, and external services

- **SQLite** `transactions.db` at the **repo root** (`AppSettings.db_path`, resolved against the project root) holds the `transactions` table plus app tables created at startup by `db_utils.setup_chat_tables`: `chat_sessions`, `chat_messages`, `request_metrics`, `query_cache`, `dispute_drafts`, `disputes`, `dispute_events`. One connection is opened at startup and shared via `app.state`.
- **Redis** (`REDIS_URL`, default `localhost:6379`) stores per-session chat history under `chat:{customer_id}:{session_id}` (TTL `REDIS_TTL`) and the SQL cache. Optional — the app degrades gracefully without it.
- **Chroma** `chroma_store/help_documents/` (`HELP_CHROMA_DIR`, ~800 chunks, committed) is the help knowledge base, built by `utils/embeddings/help_document_store.py` with the chunking strategy set in `HELP_CHUNK_STRATEGY`. `lifespan` opens it; if it is empty or fails to open, `help_vector_store` is `None` and help questions get a "help documents unavailable" reply.
- **FastAPI DI** — [src/dependencies.py](src/dependencies.py) provides `get_llm`, `get_prompts`, `get_logging`, `get_redis_client`, `get_db_conn` as request-scoped dependencies pulling from `app.state`.

### Database schema (the only table the SQL agent should target)

`transactions(transaction_id PK, customer_id, card_number, transaction_date, year, month, day, amount_usd, transaction_type, balance, merchant_brand_name, merchant_system_category1_name, merchant_system_category2_name)`. `transaction_type` is one of `income`, `payment`, `direct_debit`, `standing_order`; `amount_usd` is always positive (direction comes from `transaction_type`, not sign); `balance` is the running account balance after each transaction. The table is built by [src/clients/sqllite_db_creation.py](src/clients/sqllite_db_creation.py) from `data/data_processing/df_transaction_data_enhanced.xlsx`, which is produced by [data/data_processing/transaction_data_enhance.py](data/data_processing/transaction_data_enhance.py) (`build` reshapes the data; `shift` re-bases dates so the latest transaction is today). The supervisor prompt ([prompts/suprivisor_prompt.yml](prompts/suprivisor_prompt.yml)) and the text2sql prompt ([prompts/text2sql_generator.yml](prompts/text2sql_generator.yml)) encode this schema; keep them in sync if the schema changes.

### Data layout

- `data/data_processing/` — raw `df_transaction_data.xlsx` → `df_transaction_data_preprocessed.xlsx` (via `data/transaction_data_preprocess.py`) → `df_transaction_data_enhanced.xlsx` (via `transaction_data_enhance.py build`) → `transactions.db`.
- `data/help_documents/` — 687 Markdown help articles (`kb_NNNN_<topic>.md`) the help RAG indexes.
- `help_store/` and `help_chunks.xlsx` — the old Barclays help-centre store and its chunk dump; nothing in `src/` reads them any more.
- `tests/data/*.xlsx` — legacy spreadsheets from the food-ordering version (no expected answers); nothing reads them. The current golden set is `evaluation/e2e/golden_cases.yaml`.

## Conventions and gotchas

- **The misspelling `suprivisor` (instead of `supervisor`) is load-bearing.** It appears in the prompt filename, the `Prompts.suprivisor_prompt` field, the agent module/function names, and the `Graph_State.suprivisor_response` field. Don't "fix" it as part of an unrelated change.
- `Agent_Graph.__init__` sets `self.logger = logger` **after** `self.app = self._workflow()`, but `_workflow` doesn't use the logger so this works. Don't reorder so a node method that needs the logger gets called during compile.
- `SubGraph_State.agent_metrics` is expected to already be a dict when the subgraph starts. In the normal flow the `guardrail` agent initializes `agent_metrics = {}`. If you add a graph path that reaches the subgraph without going through `guardrail`, initialize `agent_metrics = {}` yourself.
- `_supervisor_router` can return **multiple routes** simultaneously (e.g. both `sql_generation` and `fallback` when there are multiple banksql queries). LangGraph fans these out; the `join` node synchronises them before `visualization_agent`.
- `transactions.db` is checked into the repo (~880KB). `orders.db` (~13MB) is the legacy food-ordering DB and is unused by the current code path; leave it alone unless intentionally removing legacy assets.
- The `data/embeddings_data/` FAISS indices are leftovers from the food-ordering version; `get_vectordb_data` in `file_utils.py` is not called. `chroma_store/help_documents/` is **live** (see above).
- Qwen3-8B can leak `<think>` text into answers; `CAPSTONE_DISABLE_THINKING` turns thinking off for the capstone provider.
- For the `capstone` and `qwen` providers, `with_structured_output()` defaults to **JSON mode** (`_with_json_mode_structured_output` in `clients/llm_client.py`). Schema-guided decoding made Qwen loop on whitespace until the 1,024-token cap (LengthFinishReasonError). The schema is sent as a plain field list and an unparsable reply is retried once. Don't switch these providers back to `json_schema` without re-running the e2e evaluation.
- `dashboard.py` is a minimal Streamlit app that renders `src/index.html` inside a full-screen iframe — it is just a UI shell and does not contain application logic.
