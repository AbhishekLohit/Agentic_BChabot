from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import json
import os
from contextlib import asynccontextmanager
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import utils.db_utils as db_utils
from clients.llm_client import get_configured_llm, get_embeddings
from app_models import (
                UIUserRequest,
                Graph_State,
                ChatMessage,
                UIUserRequestDebug,
                )
from app_setting import AppSettings
from utils.embeddings.help_document_store import create_help_vector_store
from langgraph.errors import GraphRecursionError
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
                    get_db_conn,
                    get_llm_embeddings,
                    get_help_vector_store
                    )
from agent_graph import Agent_Graph
from agent_subraph import Agent_SubGraph
from agents.disputes.registry import load_dispute_registry
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


def _model_choices(settings: AppSettings) -> dict:
    """UI model choice -> (provider, model). model None = that provider's configured model."""
    return {
        "haiku": ("anthropic", settings.ANTHROPIC_HAIKU_MODEL),
        "qwen": ("capstone", None),
    }


def _default_choice(settings: AppSettings, model_choices: dict) -> str | None:
    """The UI choice selected by default: UI_DEFAULT_MODEL if it could be set up,
    else the choice that is the server's own provider/model, else None."""
    preferred = (settings.UI_DEFAULT_MODEL or "").strip().lower()
    if preferred in model_choices:
        return preferred
    return next(
        (c for c, (p, m) in _model_choices(settings).items()
         if c in model_choices and p == settings.LLM_PROVIDER.lower() and m is None),
        None,
    )


def _llm_for(request, input_request, default_llm):
    """The chat model for this request: the customer's UI choice if it is available,
    otherwise the server default (app.state.llm)."""
    choice = (getattr(input_request, "model", None) or "").strip().lower()
    return getattr(request.app.state, "model_choices", {}).get(choice, default_llm)


@asynccontextmanager
async def lifespan(app: FastAPI):

    app_settings = AppSettings()

    # LangSmith tracing — activated only when API key is provided
    if app_settings.LANGCHAIN_API_KEY:
        os.environ["LANGCHAIN_TRACING_V2"] = app_settings.LANGCHAIN_TRACING_V2
        os.environ["LANGCHAIN_API_KEY"] = app_settings.LANGCHAIN_API_KEY
        os.environ["LANGCHAIN_PROJECT"] = app_settings.LANGCHAIN_PROJECT

    logger = setup_logging()
    llm = get_configured_llm()
    prompts = get_all_prompts(app_settings)

    # Models the customer can choose between in the UI. Each is built once here; one that
    # cannot be built (e.g. no ANTHROPIC_API_KEY) is simply not offered.
    model_choices = {}
    for choice, (provider, model) in _model_choices(app_settings).items():
        try:
            if provider == app_settings.LLM_PROVIDER.lower() and model is None:
                model_choices[choice] = llm
            else:
                model_choices[choice] = get_configured_llm(
                    provider=provider,
                    model=model if model else None,
                )
        except Exception as e:
            logger.warning("Model choice %r unavailable: %s", choice, e)
    default_choice = _default_choice(app_settings, model_choices)
    logger.info("Model choices available: %s (default: %s)", list(model_choices), default_choice)

    # Help-document RAG index (data/help_documents -> Chroma), used by the RAG help agent.
    # Embeddings come from EMBEDDING_PROVIDER (local MiniLM, capstone bge-small or OpenAI).
    try:
        llm_embeddings = get_embeddings(app_settings.EMBEDDING_PROVIDER)
        help_vector_store = create_help_vector_store(llm_embeddings, app_settings)
        n_chunks = help_vector_store._collection.count()
        if n_chunks == 0:
            logger.warning("Help-document index is empty — build it with "
                           "`cd src; python -m utils.embeddings.help_document_store`. "
                           "Help questions will get a 'help documents unavailable' reply until then.")
            help_vector_store = None
        else:
            logger.info("Help-document vector store loaded (%s chunks).", n_chunks)
    except Exception as e:
        logger.warning("Help-document vector store unavailable — RAG help answers disabled: %s", e)
        llm_embeddings = None
        help_vector_store = None

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
    app.state.model_choices = model_choices
    app.state.default_model_choice = default_choice
    app.state.prompts = prompts
    app.state.logger = logger
    app.state.redis_client = redis_client
    app.state.sqlite_db = db_conn
    app.state.llm_embeddings = llm_embeddings
    app.state.help_vector_store = help_vector_store
    app.state.dispute_registry = dispute_registry
    app.state.app_settings = app_settings

    yield

    if redis_client is not None:
        redis_client.close()
    db_conn.close()
    logger.info("Shutting down chatbot...")
   
app = FastAPI(title="Banking chatbot", lifespan=lifespan)

# GRAPH_CONFIG (the agent step cap, AGENT_RECURSION_LIMIT) lives in graph_runner.py.
STEP_LIMIT_MESSAGE = ("I'm sorry, I couldn't complete this request within the allowed number of steps. "
                      "Please try rephrasing your question.")

TECHNICAL_FALLBACK_MESSAGE = (
    "I'm sorry, but I'm currently unable to process your request due to a "
    "technical issue. Please try again later."
)


class LoginRequest(BaseModel):
    user_id: str
    password: str


from graph_runner import GRAPH_CONFIG, _build_agent_trace, build_agent_graph, build_graph_state, run_turn  # noqa: E402,F401




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
    openai_llm = _llm_for(request, input_request, openai_llm)
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

    # Summarise once there are HISTORY_SUMMARY_AFTER_TURNS prior user turns, so the
    # history cannot grow without bound.
    prior_user_turns = sum(1 for m in chat_history if m.role == "user") - 1
    if prior_user_turns >= app_settings.HISTORY_SUMMARY_AFTER_TURNS:
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
        help_queries= None,
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
                              db_conn=db_conn,
                              settings=app_settings,
                              dispute_registry=request.app.state.dispute_registry,
                              help_vector_store=request.app.state.help_vector_store)
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
        response = await agent_graph.app.ainvoke(graph_state, config=GRAPH_CONFIG)

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
        if final_chat_response:
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
    except GraphRecursionError:
        logger.warning(f"Agent step limit ({GRAPH_CONFIG['recursion_limit']}) reached")
        return JSONResponse({"response": STEP_LIMIT_MESSAGE, "visualization": None, "agent_trace": []})
    except Exception as e:
        logger.exception("Error while invoking agent graph")
        return JSONResponse({"error": "internal_error", "details": str(e)}, status_code=500)

    logger.info(f"Final response generated: {final_chat_response}")
    return JSONResponse({
        "response": final_chat_response,
        "visualization": viz_payload,
        "agent_trace": agent_trace,
    })


@app.post("/process-debug")
async def process_debug(
    request: Request,
    input_request: UIUserRequestDebug,
    openai_llm: Annotated[Any, Depends(get_llm)],  # type: ignore
    prompts: Annotated[Prompts, Depends(get_prompts)],  # type: ignore
    logger: Annotated[Logger, Depends(get_logging)],  # type: ignore
    redis_client: Annotated[any, Depends(get_redis_client)],  # type: ignore
    db_conn: Annotated[any, Depends(get_db_conn)],  # type: ignore
    help_vector_store: Annotated[Any, Depends(get_help_vector_store)],
):
    """Runs one question through the graph with a caller-supplied chat history and
    returns the full graph state (routing, help_source, citations, SQL, metrics).
    Nothing is saved to Redis or the chat database."""
    app_settings: AppSettings = request.app.state.app_settings
    openai_llm = _llm_for(request, input_request, openai_llm)
    logger.info(f"[DEBUG] Chat history received: {input_request.chat_history}")
    graph_state = build_graph_state(input_request.user_query, input_request.customer_id,
                                    input_request.session_id, list(input_request.chat_history))
    agent_graph = build_agent_graph(openai_llm, prompts, logger, db_conn, app_settings,
                                    dispute_registry=request.app.state.dispute_registry,
                                    help_vector_store=help_vector_store,
                                    redis_client=redis_client)
    try:
        logger.info("[DEBUG] Invoking agent graph")
        return await run_turn(agent_graph, graph_state)
    except GraphRecursionError:
        logger.warning(f"[DEBUG] Agent step limit ({GRAPH_CONFIG['recursion_limit']}) reached")
        return {"error": "step_limit", "details": STEP_LIMIT_MESSAGE}
    except Exception as e:
        logger.exception("[DEBUG] Error while invoking agent graph")
        return {"error": "internal_error", "details": str(e)}


# ---- Streaming (SSE) support -------------------------------------------------

# Friendly progress labels for the live "what the bot is doing" stream (Level 1).
_STREAM_STEP_LABELS = {
    "guardrail": "Checking your request",
    "context_builder": "Understanding your question",
    "suprivisor": "Working out what you need",
    "sql_generation": "Looking up your transactions",
    "help_center": "Searching the help documents",
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
                graph_state, config=GRAPH_CONFIG, stream_mode=["updates", "messages", "values"]
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
                    # Only stream tokens from the user-facing answer node — not the
                    # internal guardrail / SQL / supervisor LLM calls. The help node is not
                    # streamed: its RAG answer is structured JSON that is citation-checked
                    # after generation, so only the checked answer (final event) is shown.
                    if meta.get("langgraph_node") == "response_generator":
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
            if final_chat_response:
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
        except GraphRecursionError:
            logger.warning(f"Agent step limit ({GRAPH_CONFIG['recursion_limit']}) reached")
            yield _sse({"type": "final", "response": STEP_LIMIT_MESSAGE, "visualization": None, "agent_trace": []})
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


@app.post("/process-voice")
async def process_voice(
    file: UploadFile = File(...),
    openai_llm: Annotated[Any, Depends(get_llm)] = None,
    prompts: Annotated[Prompts, Depends(get_prompts)] = None,
    logger: Annotated[Logger, Depends(get_logging)] = None,
    redis_client: Annotated[any, Depends(get_redis_client)] = None,
    db_conn: Annotated[any, Depends(get_db_conn)] = None,
):
    """
    Endpoint that accepts binary audio upload, transcribes it using Whisper,
    and runs it through the chatbot pipeline.
    """
    from app_setting import AppSettings
    settings = AppSettings()
    api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Voice transcription is unavailable without an OpenAI API key. Text chat with Qwen is unaffected.",
        )

    logger.info(f"Received voice upload request: filename={file.filename}")
    
    # Save the uploaded file temporarily
    temp_file_name = f"temp_{file.filename}"
    with open(temp_file_name, "wb") as f:
        f.write(await file.read())
        
    try:
        from openai import AsyncOpenAI
            
        # Transcribe with Whisper
        client = AsyncOpenAI(api_key=api_key)
        with open(temp_file_name, "rb") as audio:
            transcription = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio
            )
            
        transcribed_text = transcription.text
        logger.info(f"Whisper transcription completed: '{transcribed_text}'")
        
        # Build UIUserRequest matching standard payload
        from app_models import UIUserRequest
        request_data = UIUserRequest(
            customer_id="Cust_0410",
            session_id="sess_voice_upload",
            user_query=transcribed_text
        )
        
        # Invoke standard process endpoint logic
        return await process(
            input_request=request_data,
            openai_llm=openai_llm,
            prompts=prompts,
            logger=logger,
            redis_client=redis_client,
            db_conn=db_conn
        )
    except Exception as e:
        logger.exception("Error during voice query processing")
        return {"error": "voice_processing_failed", "details": str(e)}
    finally:
        # Clean up temporary audio file
        if os.path.exists(temp_file_name):
            os.remove(temp_file_name)

from pydantic import BaseModel

class SettingsUpdateRequest(BaseModel):
    llm_provider: str
    temperature: float | None = 0.2
    max_tokens: int | None = 1500

@app.get("/models")
async def list_models(request: Request):
    """Chat models the UI can offer (only those that could be set up) and the default."""
    available = list(getattr(request.app.state, "model_choices", {}))
    return {"models": available, "default": getattr(request.app.state, "default_model_choice", None)}


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
