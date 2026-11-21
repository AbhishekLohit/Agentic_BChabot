# FinTell — Functional Workflow & Architecture

Detailed diagrams of the FinTell banking chatbot: every agent, router, component and
how they connect. Diagrams are [Mermaid](https://mermaid.js.org/) — they render on
GitHub and in VS Code (Markdown Preview Mermaid Support).

- **Diagram 1** — End-to-end request flow (API → outer LangGraph → inner SQL subgraph → response).
- **Diagram 2** — Runtime components & infrastructure wiring (app.state, DI, external services).
- **Diagram 3** — Help-centre RAG ingestion pipeline (offline) + retrieval (runtime).
- **Diagram 4** — Streaming (SSE) sequence.

---

## Diagram 1 — Request flow: API, agents & routers

```mermaid
flowchart TD
    %% ================= CLIENTS =================
    subgraph CLIENTS["Clients"]
        UI["Chat UI — index.html<br/>(SSE streaming, agent-trace panel)"]
    end

    %% ================= API LAYER =================
    subgraph API["FastAPI — app.py"]
        EP_PROCESS["POST /process<br/>(Redis-backed, JSON)"]
        EP_STREAM["POST /process-stream<br/>(SSE: step / token / final)"]
        PREP["_prepare_request()<br/>history seed + summarise → Graph_State + build graph"]
        TRACE["_build_agent_trace()<br/>+ _metric_dict()"]
    end

    UI --> EP_STREAM
    UI -.fallback.-> EP_PROCESS
    EP_PROCESS --> PREP
    EP_STREAM --> PREP
    PREP --> GSTART

    %% ================= OUTER GRAPH =================
    subgraph OUTER["Outer graph — agent_graph.py (Graph_State)"]
        GSTART(["START"])
        GUARD["guardrail<br/>input security check<br/>SYSTEM / SQL / PII violation<br/>inits agent_metrics = {}"]
        CB["context_builder<br/>split into self-contained<br/>contextual_queries"]
        SUP["supervisor (suprivisor)<br/>classify each sub-query →<br/>BankSQL / HelpCenter / Out_of_scope"]
        SUBGRAPH[["sql_generation<br/>(inner subgraph — see Diagram 1b)"]]
        HELP["help_center<br/>RAG: HelpStore.search →<br/>strict-RAG answer + source links"]
        FB["fallback<br/>only node that emits a<br/>user-visible error message"]
        JOIN["join<br/>branch synchronisation"]
        RESP["response_generator<br/>rephrase SQL result + pass through<br/>help answer → final reply"]
        GEND(["END"])
    end

    %% ----- routers (diamonds) -----
    R_GUARD{"_guardrail_router"}
    R_CB{"_context_builder_router"}
    R_SUP{"_supervisor_router<br/>(reads banksql / help_center /<br/>out_of_scope queues)"}
    R_AFTSQL{"_after_sql_router<br/>help_center_queries left?"}

    GSTART --> GUARD --> R_GUARD
    R_GUARD -->|violation| FB
    R_GUARD -->|ok| CB --> R_CB
    R_CB -->|fallback set| FB
    R_CB -->|ok| SUP
    R_CB -->|no queries| GEND
    SUP --> R_SUP
    R_SUP -->|"banksql ≥ 1"| SUBGRAPH
    R_SUP -->|help only| HELP
    R_SUP -->|out_of_scope only| FB
    R_SUP -->|nothing actionable| GEND

    SUBGRAPH --> R_AFTSQL
    R_AFTSQL -->|yes, mixed query| HELP
    R_AFTSQL -->|no| JOIN
    HELP --> JOIN
    FB --> JOIN
    JOIN --> RESP --> GEND

    GEND --> TRACE
    TRACE --> UI

    %% styling
    classDef agent fill:#e8f4fd,stroke:#0074a6,stroke-width:1px,color:#103040;
    classDef router fill:#fff4e6,stroke:#d98324,stroke-width:1px,color:#7a4a10;
    classDef io fill:#eef7ee,stroke:#2e7d32,color:#1b3d1b;
    class GUARD,CB,SUP,HELP,FB,JOIN,RESP,SUBGRAPH agent;
    class R_GUARD,R_CB,R_SUP,R_AFTSQL router;
    class UI io;
```

> **Note on routing:** `_supervisor_router` picks a single primary route; when a message
> mixes a BankSQL query **and** a help query it routes to `sql_generation` first, and
> `_after_sql_router` then chains into `help_center` (sequential — so both branches never
> write the shared `agent_metrics` channel concurrently).

---

## Diagram 1b — Inner SQL subgraph (agent_subraph.py · SubGraph_State)

```mermaid
flowchart TD
    SS(["START"])
    T2S["text2sql_generator<br/>NL → SQL<br/>(can_answer_with_single_query?)"]
    EXEC["sql_execution<br/>run SQL on transactions.db<br/>→ database_query_results"]
    CORR["sql_correction<br/>repair failed SQL<br/>(runs at most once)"]
    SFB["fallback (sub)<br/>sets fallback_response"]
    SEND(["END"])

    R_EXEC{"_sql_execution_router<br/>error? already corrected?"}

    SS --> T2S --> EXEC --> R_EXEC
    R_EXEC -->|success| SEND
    R_EXEC -->|error & not yet corrected| CORR --> EXEC
    R_EXEC -->|error & already corrected| SFB --> SEND

    classDef agent fill:#e8f4fd,stroke:#0074a6,color:#103040;
    classDef router fill:#fff4e6,stroke:#d98324,color:#7a4a10;
    class T2S,EXEC,CORR,SFB agent;
    class R_EXEC router;
```

> The outer graph adds this compiled subgraph **as a node** (`add_node("sql_generation",
> agent_subgraph.app)`), so `Graph_State` and `SubGraph_State` stay field-compatible
> (e.g. `text2sql_generator_response`, `database_query_results`, `sql_query_correction`,
> `banksql_queries`, `agent_metrics`).

---

## Agent reference (per-agent contract)

Every agent is `async fn(state, llm, prompt, logger) → state`, calls the LLM, computes
`agent_metrics[<name>]`, and wraps its body in try/except that sets `fallback_response`.

| Agent | File | LLM call style | Prompt |
|---|---|---|---|
| guardrail | agents/guardrails.py | structured output | hardcoded in function |
| context_builder | agents/context_builder.py | structured output | context_builder_prompt.yml |
| supervisor | agents/supervisor.py | structured output | suprivisor_prompt.yml |
| text2sql_generator | agents/text2sql_generator.py | structured output | text2sql_generator.yml |
| sql_execution | agents/sql_execution.py | — (DB call) | — |
| sql_correction | agents/sql_correction.py | structured output | sql_correction.yml |
| help_center | agents/help_center_agent.py | **streamed plain text** (`llm.astream`) | help_center_prompt.yml |
| response_generator | agents/response_generator.py | **streamed plain text** (`llm.astream`) | response_generator_prompt.yml |
| fallback | agents/fallback.py | — | — |

---

## Diagram 2 — Runtime components & infrastructure

```mermaid
flowchart LR
    subgraph LIFE["lifespan() — startup wiring (app.state)"]
        S_LLM["llm — get_configured_llm()"]
        S_PROMPTS["prompts — get_all_prompts()"]
        S_REDIS["redis_client"]
        S_DB["sqlite_db — transactions.db"]
        S_HELP["help_store — HelpStore (Chroma)"]
        S_SET["app_settings"]
    end

    subgraph DI["dependencies.py (request-scoped)"]
        D["get_llm / get_prompts / get_logging /<br/>get_redis_client / get_db_conn"]
    end

    subgraph GRAPHS["Graph construction (per request)"]
        AG["Agent_Graph(llm, prompts, logger,<br/>agent_subgraph, help_store)"]
        ASG["Agent_SubGraph(llm, prompts, logger,<br/>db_conn, redis_client)"]
    end

    subgraph EXT["External services"]
        REDIS[("Redis<br/>chat:{cust}:{sess}, TTL")]
        SQLITE[("SQLite transactions.db<br/>transactions + chat/metrics tables")]
        CHROMA[("Chroma help_store/<br/>collection: barclays_help")]
        LLMP{{"LLM provider<br/>Anthropic claude-sonnet-4-6<br/>(openai / gemini / groq swappable)"}}
        EMBP{{"OpenAI Embeddings"}}
    end

    S_LLM --> D
    S_PROMPTS --> D
    S_REDIS --> D
    S_DB --> D
    S_HELP --> D
    D --> AG
    D --> ASG
    AG --> ASG

    AG -.all agents.-> LLMP
    S_REDIS --- REDIS
    ASG --- SQLITE
    S_DB --- SQLITE
    S_HELP --- CHROMA
    S_HELP -.embeds queries.-> EMBP

    %% persistence touchpoints
    AG -.save chat history.-> REDIS
    AG -.save_message / save_metrics.-> SQLITE
```

---

## Diagram 3 — Help-centre RAG: offline ingestion + runtime retrieval

```mermaid
flowchart TD
    subgraph OFFLINE["Offline build — clients/barclays_help_ingest.py"]
        TOPICS["TOPICS — 50 curated help URLs<br/>(cards, PIN, online/app, payments,<br/>fraud & scams, statements, …)"]
        FETCH["fetch_topics()<br/>requests + retries + robots.txt<br/>BeautifulSoup content extraction"]
        CHUNK["chunk_pages()<br/>RecursiveCharacterTextSplitter<br/>(~1000 chars / 150 overlap)"]
        XLSX["write_excel()<br/>help_chunks.xlsx (one row / chunk)"]
        ADD["HelpStore.add_documents()<br/>metadata: topic, title, source_url"]
    end

    subgraph STORE["utils/embeddings/help_store.py"]
        HS["HelpStore<br/>Chroma collection 'barclays_help'<br/>cosine space"]
    end

    EMB2{{"OpenAI text-embedding-3-small"}}
    CHROMA2[("help_store/ (Chroma)")]

    TOPICS --> FETCH --> CHUNK --> XLSX
    CHUNK --> ADD --> HS
    HS -->|embed chunks| EMB2 --> CHROMA2

    subgraph RUNTIME["Runtime — help_center agent"]
        HQ["help_center_queries"]
        SEARCH["HelpStore.search(query)<br/>top_k, similarity ≥ threshold"]
        ANSWER["strict-RAG answer<br/>(only from retrieved passages,<br/>cites source URLs)"]
    end

    HQ --> SEARCH
    SEARCH -->|embed query| EMB2
    SEARCH --- CHROMA2
    SEARCH --> ANSWER
```

---

## Diagram 4 — Streaming (SSE) sequence — /process-stream

```mermaid
sequenceDiagram
    participant UI as Chat UI
    participant API as /process-stream
    participant G as graph.astream(["updates","messages","values"])
    participant RG as response_generator / help_center

    UI->>API: POST {customer_id, session_id, user_query}
    API->>G: astream(Graph_State)
    loop per node completion
        G-->>API: ("updates", {node: Δ})
        API-->>UI: data:{type:"step", label}
    end
    loop per LLM token (answer nodes only)
        RG-->>G: llm.astream() token
        G-->>API: ("messages", chunk, meta)
        API-->>UI: data:{type:"token", text}
    end
    G-->>API: ("values", final_state)
    API->>API: save Redis + SQLite, build viz + trace
    API-->>UI: data:{type:"final", response, visualization, agent_trace}
    Note over UI: render bold/links, chart, trace metrics (latency, tokens, cached)
```

---

## Data model (the only SQL target)

`transactions(transaction_id PK, customer_id, card_number, transaction_date, year, month,
day, amount_usd, transaction_type, balance, merchant_brand_name,
merchant_system_category1_name, merchant_system_category2_name)`

- `transaction_type` ∈ {income, payment, direct_debit, standing_order}
- `amount_usd` always positive (direction comes from `transaction_type`)
- `balance` = running account balance after each transaction
