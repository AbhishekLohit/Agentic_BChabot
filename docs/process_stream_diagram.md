# `/process-stream` — Single End-to-End Diagram

One diagram tracing a streaming request through every component: agents, routers,
Redis, SQLite (`transactions.db`), Chroma help store, OpenAI embeddings, the LLM, and
the SSE event lane back to the UI. (Mermaid — renders on GitHub / VS Code.)

```mermaid
flowchart TD
    %% ===================== CLIENT =====================
    UI["Chat UI (index.html)"]

    %% ===================== API LAYER =====================
    subgraph API["FastAPI · app.py"]
        EP["POST /process-stream"]
        PREP["_prepare_request()<br/>resolve history → Graph_State<br/>build Agent_Graph + Agent_SubGraph"]
        ASTREAM["agent_graph.app.astream(<br/>stream_mode=[updates, messages, values])"]
        POST["after stream: assemble final answer<br/>persist + build viz + trace"]
    end

    %% ===================== OUTER GRAPH =====================
    subgraph OUTER["Outer LangGraph · Graph_State"]
        GUARD["guardrail<br/>security check · init agent_metrics={}"]
        rG{"_guardrail_router"}
        CB["context_builder<br/>→ contextual_queries"]
        rCB{"_context_builder_router"}
        SUP["supervisor (suprivisor)<br/>classify: BankSQL / HelpCenter / Out_of_scope"]
        rSUP{"_supervisor_router"}
        rAFT{"_after_sql_router<br/>help left?"}
        HELP["help_center<br/>strict-RAG answer (streams tokens)"]
        FB["fallback<br/>user-visible error"]
        JOIN["join"]
        VIZ["visualization_agent<br/>Plotly JSON (skips if no data)"]
        RESP["response_generator<br/>final reply (streams tokens)"]
        GEND(["END"])
    end

    %% ===================== SQL SUBGRAPH =====================
    subgraph SUB["sql_generation subgraph · SubGraph_State"]
        T2S["text2sql_generator<br/>NL → SQL"]
        EXEC["sql_execution"]
        rEX{"_sql_execution_router"}
        CORR["sql_correction<br/>(once)"]
        SFB["fallback (sub)"]
    end

    %% ===================== EXTERNAL SERVICES =====================
    subgraph EXT["External services"]
        REDIS[("Redis<br/>chat:{cust}:{sess} · TTL")]
        SQLDB[("SQLite transactions.db<br/>transactions + chat/metrics")]
        CHROMA[("Chroma help_store<br/>barclays_help")]
        EMB{{"OpenAI Embeddings"}}
        LLM{{"LLM provider<br/>Anthropic claude-sonnet-4-6"}}
    end

    %% ===================== SSE EVENT LANE =====================
    subgraph SSE["SSE events → UI"]
        EVSTEP["step  (live progress)"]
        EVTOK["token (answer typing)"]
        EVFIN["final (response + viz + trace)"]
    end

    %% --------- request setup ---------
    UI -->|"POST query"| EP --> PREP
    PREP -->|get_chat_history| REDIS
    PREP -->|seed recent msgs| SQLDB
    PREP -.summarise if long.-> LLM
    PREP --> ASTREAM --> GUARD

    %% --------- agent spine ---------
    GUARD --> rG
    rG -->|violation| FB
    rG -->|ok| CB --> rCB
    rCB -->|fallback| FB
    rCB -->|ok| SUP --> rSUP
    rCB -->|no queries| GEND
    rSUP -->|"banksql ≥ 1"| T2S
    rSUP -->|help only| HELP
    rSUP -->|out_of_scope| FB
    rSUP -->|nothing| GEND

    %% --------- sql subgraph ---------
    T2S --> EXEC --> rEX
    rEX -->|success| rAFT
    rEX -->|error, not corrected| CORR --> EXEC
    rEX -->|error, corrected| SFB --> rAFT
    EXEC <-->|run SQL| SQLDB

    rAFT -->|mixed: help left| HELP
    rAFT -->|no| JOIN
    HELP --> JOIN
    FB --> JOIN
    JOIN --> VIZ --> RESP --> GEND

    %% --------- help RAG ---------
    HELP -->|search| CHROMA
    HELP -.embed query.-> EMB
    CHROMA --- EMB

    %% --------- LLM usage ---------
    GUARD -.-> LLM
    CB -.-> LLM
    SUP -.-> LLM
    T2S -.-> LLM
    CORR -.-> LLM
    VIZ -.-> LLM
    HELP -.token stream.-> LLM
    RESP -.token stream.-> LLM

    %% --------- stream modes → SSE ---------
    ASTREAM -. "updates: node done" .-> EVSTEP
    RESP -. "messages: tokens" .-> EVTOK
    HELP -. "messages: tokens" .-> EVTOK
    GEND -->|"values: final_state"| POST

    %% --------- persistence + final ---------
    POST -->|save_chat_history| REDIS
    POST -->|save_message / save_metrics| SQLDB
    POST --> EVFIN

    EVSTEP --> UI
    EVTOK --> UI
    EVFIN --> UI

    %% ===================== STYLING =====================
    classDef agent fill:#e8f4fd,stroke:#0074a6,color:#103040;
    classDef router fill:#fff4e6,stroke:#d98324,color:#7a4a10;
    classDef svc fill:#eef7ee,stroke:#2e7d32,color:#1b3d1b;
    classDef sse fill:#f3e8fd,stroke:#7c3aed,color:#3b1d6e;
    class GUARD,CB,SUP,HELP,FB,JOIN,VIZ,RESP,T2S,EXEC,CORR,SFB agent;
    class rG,rCB,rSUP,rAFT,rEX router;
    class REDIS,SQLDB,CHROMA,EMB,LLM svc;
    class EVSTEP,EVTOK,EVFIN sse;
```

**Legend**

- **Blue** = agents · **Orange** = routers · **Green** = external services · **Purple** = SSE events.
- Solid arrows = control flow; dotted arrows = service calls (LLM / embeddings / token streams).
- `astream` runs three stream modes at once: `updates` → **step** events, `messages` →
  **token** events (only `response_generator` / `help_center` are surfaced), `values` →
  the final state used for persistence and the **final** event.
