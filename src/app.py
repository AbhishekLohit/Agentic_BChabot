from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import json
import os
from contextlib import asynccontextmanager
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import utils.db_utils as db_utils
from clients.llm_client import get_configured_llm
from app_models import (
                UIUserRequest,
                Graph_State,
                ChatMessage,
                )
from app_setting import AppSettings
from utils.file_utils import (
                            get_all_prompts,
                            save_chat_history,
                            save_raw_chat_history,
                            get_chat_history)
from agents.history_summarizer import summarize_chat_history
from utils import agent_utils
from dependencies import (
                    get_llm, 
                    get_prompts, 
                    get_logging,
                    get_redis_client,
                    get_db_conn
                    )
from agent_graph import Agent_Graph
from agent_subraph import Agent_SubGraph
from agents.disputes.registry import load_dispute_registry
from utils.embeddings.help_store import HelpStore
from typing import Annotated, Any
from logging_setup import setup_logging
from app_models import Prompts
from logging import Logger
from pydantic import BaseModel
import uvicorn
import redis
import sqlite3
import hmac

limiter = Limiter(key_func=get_remote_address)


def _resolve_path(path: str) -> str:
    """Resolve a possibly-relative path against the project root (parent of src/)."""
    if os.path.isabs(path):
        return path
    project_root = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(project_root, path)


@asynccontextmanager
async def lifespan(app: FastAPI):

    app_settings = AppSettings()

    # LangSmith tracing — activated only when API key is provided
    if app_settings.LANGCHAIN_API_KEY:
        os.environ["LANGCHAIN_TRACING_V2"] = app_settings.LANGCHAIN_TRACING_V2
        os.environ["LANGCHAIN_API_KEY"] = app_settings.LANGCHAIN_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = app_settings.LANGCHAIN_PROJECT

    llm = get_configured_llm()
    prompts = get_all_prompts(app_settings)
    logger = setup_logging()

    # Barclays help-centre RAG store. Opening the persisted Chroma dir is cheap;
    # if the KB hasn't been built yet (or fails to open) the help branch degrades
    # gracefully to a "couldn't find it" reply rather than crashing startup.
    try:
        help_store = HelpStore(
            persist_directory=_resolve_path(app_settings.help_store_dir),
            collection_name=app_settings.help_collection,
            model=app_settings.EMBED_MODEL,
            top_k=app_settings.help_top_k,
            threshold=app_settings.help_similarity_threshold,
        )
        logger.info("Help store loaded (%s chunks).", help_store.count())
    except Exception as e:
        logger.warning("Help store unavailable — help-centre answers disabled: %s", e)
        help_store = None

    # Per-reason question sets for the Disputes agent. If the file is missing or invalid
    # the graph runs without disputes (a dispute request gets the out-of-scope reply).
    try:
        dispute_registry = load_dispute_registry(app_settings.dispute_types_path)
        logger.info("Dispute registry loaded (%s reasons).", len(dispute_registry.types))
    except Exception as e:
        logger.warning("Dispute registry unavailable — dispute handling disabled: %s", e)
        dispute_registry = None

    # Graceful Redis fallback — app starts even when Redis is unavailable
    try:
        redis_client = redis.from_url(
            app_settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        redis_client.ping()
    except Exception:
        logger.warning("Redis unavailable at %s — chat history persistence disabled.", app_settings.REDIS_URL)
        redis_client = None

    # Path to your SQLite database
    DB_PATH = app_settings.db_path
    if not os.path.isabs(DB_PATH):
        project_root = os.path.dirname(os.path.dirname(__file__))
        DB_PATH = os.path.join(project_root, DB_PATH)

    # Connect to the database and ensure chat/metrics tables exist
    db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    db_conn.row_factory = sqlite3.Row
    db_utils.setup_chat_tables(db_conn)

    # Register rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Initialize api state
    app.state.llm = llm
    app.state.prompts = prompts
    app.state.logger = logger
    app.state.redis_client = redis_client
    app.state.sqlite_db = db_conn
    app.state.help_store = help_store
    app.state.dispute_registry = dispute_registry
    app.state.app_settings = app_settings

    yield

    if redis_client is not None:
        redis_client.close()
    db_conn.close()
    logger.info("Shutting down chatbot...")
   
app = FastAPI(title="Banking chatbot", lifespan=lifespan)

TECHNICAL_FALLBACK_MESSAGE = (
    "I'm sorry, but I'm currently unable to process your request due to a "
    "technical issue. Please try again later."
)


class LoginRequest(BaseModel):
    user_id: str
    password: str


def _metric_dict(metric: Any) -> dict | None:
    """Reduce a per-agent Metrics object (or dict) to the demo-relevant subset."""
    if metric is None:
        return None
    if hasattr(metric, "model_dump"):
        metric = metric.model_dump()
    if not isinstance(metric, dict):
        return None
    return {
        "model_used": metric.get("model_used"),
        "total_tokens": metric.get("total_tokens"),
        "latency": metric.get("latency"),
        "cached_tokens": metric.get("cached_tokens"),
    }


def _sum_metrics(metrics: dict, *names: str) -> dict | None:
    """Combine the metrics of agents that can run more than once in a request
    (stored as name, name_2, name_3, ...)."""
    items = [
        _metric_dict(m) for key, m in metrics.items()
        if any(key == n or key.startswith(f"{n}_") for n in names)
    ]
    items = [m for m in items if m]
    if not items:
        return None
    return {
        "model_used": items[0].get("model_used"),
        "total_tokens": sum(m.get("total_tokens") or 0 for m in items),
        "latency": round(sum(m.get("latency") or 0 for m in items), 3),
        "cached_tokens": sum(m.get("cached_tokens") or 0 for m in items),
    }


def _build_agent_trace(response: dict) -> list[dict]:
    """Assemble an ordered, UI-friendly trace of what each backend agent did.

    Reads only the intermediate outputs already present in the LangGraph result
    (and `agent_metrics`). Purely descriptive — never raises on missing fields.
    """
    metrics = response.get("agent_metrics") or {}

    def metric(name: str) -> dict | None:
        return _metric_dict(metrics.get(name))

    fallback = response.get("fallback_response")
    fallback_node = getattr(fallback, "fallback_node", None) if fallback else None
    fallback_active = bool(fallback and getattr(fallback, "fallback_flag", False)) or bool(fallback_node)

    trace: list[dict] = []

    # 1) Guardrail
    guardrail_tripped = fallback_node == "guardrail"
    trace.append({
        "agent": "guardrail",
        "title": "Guardrail",
        "status": "fallback" if guardrail_tripped else "ok",
        "summary": (
            f"Blocked: {getattr(fallback, 'fallback_reason', 'violation')}"
            if guardrail_tripped else "No violations detected"
        ),
        "details": {},
        "metrics": metric("guardrail"),
    })

    # 1b) Dispute gate: only shown when a dispute was already open for this session
    gate_kind = response.get("dispute_gate_kind")
    gate_open = gate_kind is not None
    if gate_open:
        gate_summaries = {
            "dispute": "Reply to the open dispute",
            "side_question": "Unrelated question while a dispute is open",
            "both": "Reply to the dispute plus an unrelated question",
            "new_dispute": "Customer started a different dispute",
        }
        trace.append({
            "agent": "dispute_gate",
            "title": "Dispute Gate",
            "status": "ok",
            "summary": gate_summaries.get(gate_kind, str(gate_kind)),
            "details": {"message_kind": gate_kind, "side_question": response.get("pipeline_query")},
            "metrics": _sum_metrics(metrics, "disputes_extraction"),
        })

    # 2) Context builder
    cb = response.get("context_builder_response")
    if cb is not None:
        queries = getattr(cb, "contextual_queries", []) or []
        trace.append({
            "agent": "context_builder",
            "title": "Context Builder",
            "status": "ok",
            "summary": f"Resolved {len(queries)} self-contained quer{'y' if len(queries) == 1 else 'ies'}",
            "details": {"contextual_queries": list(queries)},
            "metrics": metric("context_builder"),
        })

    # 3) Supervisor
    sup = response.get("suprivisor_response")
    if sup is not None:
        classified = [
            {
                "sub_query": getattr(c, "sub_query", ""),
                "class_label": getattr(c, "class_label", ""),
                "transaction_query_type": getattr(c, "transaction_query_type", ""),
            }
            for c in (getattr(sup, "classified_sub_queries", []) or [])
        ]
        bank = response.get("banksql_queries") or []
        help_q = response.get("help_center_queries") or []
        oos = response.get("out_of_scope_queries") or []
        disp = response.get("dispute_queries") or []
        trace.append({
            "agent": "supervisor",
            "title": "Supervisor",
            "status": "ok",
            "summary": (
                f"{len(bank)} bank quer{'y' if len(bank) == 1 else 'ies'}, {len(help_q)} help, "
                + (f"{len(disp)} dispute, " if disp else "")
                + f"{len(oos)} out-of-scope"
            ),
            "details": {
                "classified": classified,
                "banksql_queries": list(bank),
                "help_center_queries": list(help_q),
                "dispute_queries": list(disp),
                "out_of_scope_queries": list(oos),
            },
            "metrics": metric("suprivisor"),
        })

    # 3a) Disputes agent
    dispute = response.get("dispute_result")
    if dispute is not None:
        trace.append({
            "agent": "disputes",
            "title": "Disputes",
            "status": "fallback" if dispute.status == "error" else "ok",
            "summary": f"Status: {dispute.status}",
            "details": {"status": dispute.status, "summary": dispute.summary, "data": dispute.data},
            # the gate already accounts for its own extraction call
            "metrics": _sum_metrics(metrics, *(("dispute_reply",) if gate_open else ("disputes_extraction", "dispute_reply"))),
        })

    # 3b) Help centre (RAG) branch
    help_resp = response.get("help_center_response")
    help_q = response.get("help_center_queries") or []
    if help_resp is not None or help_q:
        trace.append({
            "agent": "help_center",
            "title": "Help Centre",
            "status": "ok",
            "summary": f"Answered {len(help_q)} help quer{'y' if len(help_q) == 1 else 'ies'} from the Barclays help KB",
            "details": {"help_center_queries": list(help_q)},
            "metrics": metric("help_center"),
        })

    # 4) Text-to-SQL (+ optional correction)
    t2s = response.get("text2sql_generator_response")
    if t2s is not None:
        sql_query = getattr(t2s, "sql_query", "") or ""
        trace.append({
            "agent": "text2sql_generator",
            "title": "Text-to-SQL",
            "status": "ok",
            "summary": "Generated SQL query",
            "details": {"sql_query": sql_query},
            "metrics": metric("text2sql_generator"),
        })
        if response.get("sql_query_correction"):
            trace.append({
                "agent": "sql_correction",
                "title": "SQL Correction",
                "status": "ok",
                "summary": "Repaired a failed SQL query",
                "details": {},
                "metrics": metric("sql_correction"),
            })

        # 5) SQL execution (DB call — no LLM metrics)
        rows = response.get("database_query_results")
        if rows is not None:
            trace.append({
                "agent": "sql_execution",
                "title": "SQL Execution",
                "status": "ok",
                "summary": f"Returned {len(rows)} row{'' if len(rows) == 1 else 's'}",
                "details": {"row_count": len(rows), "sample_rows": [str(r) for r in rows[:5]]},
                "metrics": None,
            })

    # 6) Visualization
    viz = response.get("visualization_response")
    if viz is not None:
        decision = getattr(viz, "decision", None)
        should = bool(getattr(decision, "should_visualize", False)) if decision else False
        rendered = bool(getattr(viz, "plotly_json", None))
        trace.append({
            "agent": "visualization_agent",
            "title": "Visualization",
            "status": "ok" if should else "skipped",
            "summary": (
                f"{getattr(decision, 'chart_type', 'chart')} chart"
                if should else "No chart needed"
            ),
            "details": {
                "chart_type": getattr(decision, "chart_type", None) if decision else None,
                "chart_title": getattr(decision, "chart_title", None) if decision else None,
                "reasoning": getattr(decision, "reasoning", None) if decision else None,
                "rendered": rendered,
            },
            "metrics": metric("visualization_agent"),
        })

    # 7) Response generator
    rg = response.get("response_generation_response")
    if rg is not None:
        trace.append({
            "agent": "response_generator",
            "title": "Response Generator",
            "status": "ok",
            "summary": "Rephrased results into a chat reply",
            "details": {"generated_response": getattr(rg, "generated_response", "")},
            "metrics": metric("response_generator"),
        })

    # 8) Fallback (non-guardrail)
    if fallback_active and fallback_node and fallback_node != "guardrail":
        trace.append({
            "agent": "fallback",
            "title": "Fallback",
            "status": "fallback",
            "summary": f"Triggered at '{fallback_node}'",
            "details": {
                "fallback_node": fallback_node,
                "fallback_reason": getattr(fallback, "fallback_reason", None),
            },
            "metrics": None,
        })

    return trace


@app.get("/", response_class=HTMLResponse)
async def read_index():
    base_dir = os.path.dirname(__file__)
    index_path = os.path.join(base_dir, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)


@app.post("/login")
async def login(req: LoginRequest):
    allowed_users = {
        user.strip()
        for user in os.getenv("DASHBOARD_USER_IDS", "Cust_0410,Cust_0411,Cust_0100").split(",")
        if user.strip()
    }
    expected_password = os.getenv("DASHBOARD_PASSWORD", "bbot@123")

    user_ok = req.user_id in allowed_users
    password_ok = hmac.compare_digest(req.password, expected_password)

    if not user_ok or not password_ok:
        return {"status": "error", "message": "Invalid user ID or password."}

    return {"status": "success", "user_id": req.user_id}


async def _prepare_request(request, input_request, openai_llm, prompts, logger, redis_client, db_conn):
    """Shared setup for /process and /process-stream: resolve chat history (with
    rolling-window seeding + summarisation) and build the graph + initial state.
    Returns (app_settings, chat_history, graph_state, agent_graph)."""
    app_settings: AppSettings = request.app.state.app_settings
    chat_history = get_chat_history(
        redis_client,
        input_request.customer_id,
        input_request.session_id,
        input_request.user_query,
    )

    # Fresh conversation (Redis holds nothing for this session yet, so chat_history
    # is just the current message): seed the context builder with this customer's
    # messages from the last N hours — a rolling window computed at conversation start.
    if len(chat_history) == 1 and app_settings.HISTORY_LOOKBACK_HOURS > 0:
        recent = db_utils.get_recent_messages(
            db_conn,
            input_request.customer_id,
            hours=app_settings.HISTORY_LOOKBACK_HOURS,
            exclude_session_id=input_request.session_id,
            limit=app_settings.HISTORY_LOOKBACK_MAX_MESSAGES,
        )
        if recent:
            seed = [ChatMessage(role=m["role"], content=m["content"]) for m in recent]
            chat_history = seed + chat_history
            logger.info(
                f"Seeded {len(seed)} message(s) from the last "
                f"{app_settings.HISTORY_LOOKBACK_HOURS}h into the new conversation."
            )

    # Summarize if >= 5 prior user turns to prevent unbounded context growth
    prior_user_turns = sum(1 for m in chat_history if m.role == "user") - 1
    if prior_user_turns >= 5:
        current_msg = chat_history[-1]
        compressed = await summarize_chat_history(
            chat_history[:-1],
            openai_llm,
            prompts.history_summarizer_prompt,
            logger,
        )
        chat_history = compressed + [current_msg]
        save_raw_chat_history(
            redis_client,
            input_request.customer_id,
            input_request.session_id,
            compressed,
            app_settings.REDIS_TTL,
        )

    logger.info(f"Chat history retrieved: {chat_history}")
    graph_state = Graph_State(
        input_request = input_request,
        chat_history = chat_history,
        customer_id = input_request.customer_id,
        context_builder_response = None,
        suprivisor_response = None,
        text2sql_generator_response= None,
        database_query_results = None,
        banksql_queries= None,
        out_of_scope_queries= None,
        fallback_response = None,
        response_generation_response = None,
        agent_metrics=None
    )
    agent_subgraph = Agent_SubGraph(openai_llm=openai_llm,
                              prompts=prompts,
                              logger=logger,
                              db_conn=db_conn,
                              redis_client=redis_client)
    agent_graph = Agent_Graph(openai_llm=openai_llm,
                              prompts=prompts,
                              logger=logger,
                              agent_subgraph=agent_subgraph,
                              help_store=request.app.state.help_store,
                              db_conn=db_conn,
                              settings=app_settings,
                              dispute_registry=request.app.state.dispute_registry)
    return app_settings, chat_history, graph_state, agent_graph


@app.post("/process")
@limiter.limit("20/minute")
async def process(
    request: Request,
    input_request : UIUserRequest,
    openai_llm : Annotated[Any,Depends(get_llm)], # type: ignore
    prompts : Annotated[Prompts,Depends(get_prompts)], # type: ignore
    logger: Annotated[Logger,Depends(get_logging)], # type: ignore
    redis_client: Annotated[any,Depends(get_redis_client)], # type: ignore
    db_conn: Annotated[any,Depends(get_db_conn)], # type: ignore
):
    app_settings, chat_history, graph_state, agent_graph = await _prepare_request(
        request, input_request, openai_llm, prompts, logger, redis_client, db_conn
    )

    try:
        logger.info("Process endpoint invoked sucessfully")
        response = await agent_graph.app.ainvoke(graph_state)

        final_chat_response = response["response_generation_response"].generated_response

        save_chat_history(
            redis_client,
            input_request.customer_id,
            input_request.session_id,
            chat_history,
            final_chat_response,
            logger,
            ttl_seconds=app_settings.REDIS_TTL,
        )

        # Persist conversation messages and token metrics to SQLite
        now = db_utils.now_iso()
        db_utils.upsert_session(
            db_conn,
            input_request.session_id,
            input_request.customer_id,
            input_request.user_query,
            now,
        )
        db_utils.save_message(db_conn, input_request.session_id, input_request.customer_id, "user", input_request.user_query, now)
        db_utils.save_message(db_conn, input_request.session_id, input_request.customer_id, "assistant", final_chat_response, now)
        agent_metrics = response.get("agent_metrics")
        if agent_metrics:
            db_utils.save_metrics(db_conn, input_request.customer_id, input_request.session_id, agent_metrics, now)

        # Build visualization payload (None when no chart was produced)
        viz_payload = None
        viz_response = response.get("visualization_response")
        if viz_response and viz_response.decision.should_visualize and viz_response.plotly_json:
            viz_payload = {
                "should_visualize": True,
                "chart_type": viz_response.decision.chart_type,
                "chart_title": viz_response.decision.chart_title,
                "plotly_json": viz_response.plotly_json,
            }

        # Build a step-by-step trace of each agent's work for the UI's backend panel
        agent_trace = _build_agent_trace(response)

        logger.info("Final response generated")
    except Exception as e:
        logger.exception("Error while invoking agent graph")
        return JSONResponse({"error": "internal_error", "details": str(e)}, status_code=500)

    logger.info(f"Final response generated: {final_chat_response}")
    return JSONResponse({
        "response": final_chat_response,
        "visualization": viz_payload,
        "agent_trace": agent_trace,
    })


# ---- Streaming (SSE) support -------------------------------------------------

# Friendly progress labels for the live "what the bot is doing" stream (Level 1).
_STREAM_STEP_LABELS = {
    "guardrail": "Checking your request",
    "context_builder": "Understanding your question",
    "suprivisor": "Working out what you need",
    "sql_generation": "Looking up your transactions",
    "help_center": "Searching the Barclays help centre",
    "visualization_agent": "Preparing a chart",
    "dispute_agent": "Reviewing your dispute",
    "response_generator": "Writing your answer",
    "fallback": "Preparing a response",
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/process-stream")
@limiter.limit("20/minute")
async def process_stream(
    request: Request,
    input_request: UIUserRequest,
    openai_llm: Annotated[Any, Depends(get_llm)],  # type: ignore
    prompts: Annotated[Prompts, Depends(get_prompts)],  # type: ignore
    logger: Annotated[Logger, Depends(get_logging)],  # type: ignore
    redis_client: Annotated[any, Depends(get_redis_client)],  # type: ignore
    db_conn: Annotated[any, Depends(get_db_conn)],  # type: ignore
):
    """Server-Sent Events variant of /process. Streams live progress steps
    (Level 1) and the answer token-by-token (Level 2), then a final event with the
    full response, visualization and agent trace."""
    app_settings, chat_history, graph_state, agent_graph = await _prepare_request(
        request, input_request, openai_llm, prompts, logger, redis_client, db_conn
    )

    async def event_gen():
        final_state: dict = {}
        streamed = ""   # the token text sent so far
        try:
            async for mode, chunk in agent_graph.app.astream(
                graph_state, stream_mode=["updates", "messages", "values"]
            ):
                if mode == "values":
                    # Full state after each step; keep the last as the final state.
                    final_state = chunk
                elif mode == "updates":
                    # {node_name: state_delta} — emit a progress step per completed node.
                    for node in (chunk or {}).keys():
                        label = _STREAM_STEP_LABELS.get(node)
                        if label:
                            yield _sse({"type": "step", "agent": node, "label": label})
                elif mode == "messages":
                    msg_chunk, meta = chunk
                    # Only stream tokens from the user-facing answer nodes — not the
                    # internal guardrail / SQL / supervisor LLM calls.
                    if meta.get("langgraph_node") in ("response_generator", "help_center"):
                        text = agent_utils.message_to_text(msg_chunk)
                        if text:
                            streamed += text
                            yield _sse({"type": "token", "text": text})

            # ---- Stream complete: assemble final answer + persist (mirrors /process) ----
            resp_obj = final_state.get("response_generation_response")
            final_chat_response = (
                resp_obj.generated_response if resp_obj else TECHNICAL_FALLBACK_MESSAGE
            )

            # Text added to the reply after the model finished (the "your dispute is still
            # open" reminder after a side question) is not model output, so it was not
            # streamed. Send that tail so the live text matches the final reply.
            if streamed and len(final_chat_response) > len(streamed) and final_chat_response.startswith(streamed):
                yield _sse({"type": "token", "text": final_chat_response[len(streamed):]})

            save_chat_history(
                redis_client, input_request.customer_id, input_request.session_id,
                chat_history, final_chat_response, logger, ttl_seconds=app_settings.REDIS_TTL,
            )
            now = db_utils.now_iso()
            db_utils.upsert_session(db_conn, input_request.session_id, input_request.customer_id, input_request.user_query, now)
            db_utils.save_message(db_conn, input_request.session_id, input_request.customer_id, "user", input_request.user_query, now)
            db_utils.save_message(db_conn, input_request.session_id, input_request.customer_id, "assistant", final_chat_response, now)
            metrics = final_state.get("agent_metrics")
            if metrics:
                db_utils.save_metrics(db_conn, input_request.customer_id, input_request.session_id, metrics, now)

            viz_payload = None
            viz_response = final_state.get("visualization_response")
            if viz_response and viz_response.decision.should_visualize and viz_response.plotly_json:
                viz_payload = {
                    "should_visualize": True,
                    "chart_type": viz_response.decision.chart_type,
                    "chart_title": viz_response.decision.chart_title,
                    "plotly_json": viz_response.plotly_json,
                }

            agent_trace = _build_agent_trace(final_state)

            yield _sse({
                "type": "final",
                "response": final_chat_response,
                "visualization": viz_payload,
                "agent_trace": agent_trace,
            })
        except Exception:
            logger.exception("Error while streaming agent graph")
            yield _sse({"type": "error", "message": TECHNICAL_FALLBACK_MESSAGE})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/health")
async def health(db_conn: Annotated[any, Depends(get_db_conn)], redis_client: Annotated[any, Depends(get_redis_client)]):
    redis_ok = False
    if redis_client is not None:
        try:
            redis_client.ping()
            redis_ok = True
        except Exception:
            pass
    try:
        db_conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "redis": redis_ok, "db": db_ok}


@app.get("/chat-history/{customer_id}")
async def get_chat_history_endpoint(
    customer_id: str,
    db_conn: Annotated[any, Depends(get_db_conn)],
):
    sessions = db_utils.get_sessions(db_conn, customer_id)
    result = []
    for session in sessions:
        messages = db_utils.get_messages(db_conn, session["session_id"])
        result.append({**session, "messages": messages})
    return JSONResponse(result)


@app.delete("/chat-history/{customer_id}")
async def clear_chat_history_endpoint(
    customer_id: str,
    db_conn: Annotated[any, Depends(get_db_conn)],
    keep_session_id: str | None = None,
):
    """
    Remove all of the customer's older conversations, keeping only the current
    session (keep_session_id). Used by the 'clear older chats' button in the UI.
    """
    deleted = db_utils.delete_other_sessions(db_conn, customer_id, keep_session_id or "")
    return JSONResponse({"status": "success", "deleted_sessions": deleted})


class SettingsUpdateRequest(BaseModel):
    llm_provider: str
    temperature: float | None = 0.2
    max_tokens: int | None = 1500

@app.post("/update-settings")
async def update_settings(req: SettingsUpdateRequest):
    """
    Endpoint to dynamically update the active LLM configuration in app state.
    """
    import logging
    logger = logging.getLogger("app_logger")
    logger.info(f"Received request to update LLM configuration: provider={req.llm_provider}, temp={req.temperature}, max_tokens={req.max_tokens}")
    try:
        new_llm = get_configured_llm(
            provider=req.llm_provider,
            temperature=req.temperature,
            max_tokens=req.max_tokens
        )
        app.state.llm = new_llm
        logger.info("Successfully updated active LLM model in app.state.llm.")
        return {"status": "success", "provider": req.llm_provider, "temperature": req.temperature}
    except Exception as e:
        logger.exception("Failed to update active LLM model.")
        return {"status": "error", "details": str(e)}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=dev_mode, reload_dirs=["src", "prompts"] if dev_mode else None)
