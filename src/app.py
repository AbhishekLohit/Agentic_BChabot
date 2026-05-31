from fastapi import Depends, FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from clients.llm_client import get_configured_llm, get_openai_embeddings
from app_models import (
                UIUserRequest,
                Graph_State,
                ResponseGeneratorResponse,
                ChatMessage,
                UIUserRequestDebug,
                Text2SqlGeneratorResponse,
                FallbackResponse,
                )
from app_setting import AppSettings
from utils.file_utils import (
                            get_all_prompts,
                            save_chat_history,
                            get_chat_history,
                            get_vectordb_data)
from dependencies import (
                    get_llm, 
                    get_prompts, 
                    get_logging,
                    get_redis_client,
                    get_db_conn,
                    get_user_data,
                    get_llm_embeddings
                    )
from agent_graph import Agent_Graph
from agent_subraph import Agent_SubGraph
from typing import Annotated, Any
from logging_setup import setup_logging
from app_models import Prompts
from logging import Logger
from datetime import datetime
from pydantic import BaseModel
import uvicorn
import redis
import sqlite3
import re
import hmac

@asynccontextmanager
async def lifespan(app: FastAPI):

    app_settings = AppSettings()
    llm = get_configured_llm()
    llm_embeddings = get_openai_embeddings(model="text-embedding-3-small")
    prompts = get_all_prompts(app_settings)
    logger = setup_logging()
    
    redis_client = redis.from_url(
        app_settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )
    # Path to your SQLite database
    DB_PATH = app_settings.db_path
    if not os.path.isabs(DB_PATH):
        project_root = os.path.dirname(os.path.dirname(__file__))
        DB_PATH = os.path.join(project_root, DB_PATH)

    # Connect to the database
    db_conn = sqlite3.connect(DB_PATH)

    # Initialize api state
    app.state.llm = llm
    app.state.prompts = prompts
    app.state.logger = logger  # make logger available via app.state
    app.state.redis_client = redis_client
    app.state.sqlite_db = db_conn
    app.state.llm_embeddings = llm_embeddings
    # app.state.user_data = user_data
    # Connect DB or load your LangGraph workflow here
    # app.state.graph = MyLangGraphSupervisor()
    
    yield
    redis_client.close()
    db_conn.close()
    logger.info("Shutting down chatbot...")
    # Close DB connections or cleanup resources
   
app = FastAPI(title="Banking chatbot", lifespan=lifespan)

from fastapi.responses import HTMLResponse
import os

TECHNICAL_FALLBACK_MESSAGE = (
    "I'm sorry, but I'm currently unable to process your request due to a "
    "technical issue. Please try again later."
)


class LoginRequest(BaseModel):
    user_id: str
    password: str


def _rows_as_strings(cursor: sqlite3.Cursor, rows: list[tuple]) -> list[str]:
    columns = [col[0] for col in cursor.description or []]
    return [str(dict(zip(columns, row))) for row in rows]


def _local_transaction_fallback(
    input_request: UIUserRequest,
    db_conn: sqlite3.Connection,
) -> tuple[str, list[str], str] | None:
    """Answer common dashboard prompts directly from SQLite when the LLM provider is unavailable."""
    query_text = input_request.user_query.strip()
    query_lower = query_text.lower()
    customer_id = input_request.customer_id
    cursor = db_conn.cursor()

    try:
        if query_lower in {"hi", "hello", "hey", "hii", "good morning", "good afternoon", "good evening"}:
            return (
                "Hi, I'm ready to help with your transactions. You can ask about spending by category, merchant history, card type, country, or highest transaction.",
                [],
                "",
            )

        spend_words = ["spend", "spending", "spent", "suspended", "spended"]
        asks_about_spend = any(word in query_lower for word in spend_words)

        if asks_about_spend and "last month" in query_lower:
            today = datetime.now()
            last_month_year = today.year
            last_month = today.month - 1
            if last_month == 0:
                last_month = 12
                last_month_year -= 1

            sql_query = """
                SELECT SUM(amount_usd) AS total_spent
                FROM transactions
                WHERE customer_id = ?
                  AND year = ?
                  AND month = ?
            """
            cursor.execute(sql_query, (customer_id, last_month_year, last_month))
            total = cursor.fetchone()[0] or 0.0
            month_name = datetime(last_month_year, last_month, 1).strftime("%B %Y")
            return (
                f"You spent a total of ${total:,.2f} last month ({month_name}).",
                [str({"total_spent": total, "year": last_month_year, "month": last_month})],
                sql_query,
            )

        category = None
        for candidate in AppSettings().MERCHANT_CATEGORIES:
            if candidate.lower() in query_lower:
                category = candidate
                break

        if category and asks_about_spend:
            sql_query = """
                SELECT SUM(amount_usd) AS total_spent
                FROM transactions
                WHERE customer_id = ?
                  AND LOWER(merchant_system_category1_name) = LOWER(?)
            """
            cursor.execute(sql_query, (customer_id, category))
            total = cursor.fetchone()[0] or 0.0
            return (
                f"You spent a total of ${total:,.2f} on {category}.",
                [str({"total_spent": total})],
                sql_query,
            )

        if "highest" in query_lower and "transaction" in query_lower:
            sql_query = """
                SELECT transaction_id, transaction_date, amount_usd, merchant_brand_name,
                       merchant_system_category1_name
                FROM transactions
                WHERE customer_id = ? AND year = ?
                ORDER BY amount_usd DESC
                LIMIT 1
            """
            cursor.execute(sql_query, (customer_id, datetime.now().year))
            rows = cursor.fetchall()
            if rows:
                row = dict(zip([col[0] for col in cursor.description], rows[0]))
                return (
                    f"Your highest transaction this year was ${row['amount_usd']:,.2f} at {row['merchant_brand_name']} on {row['transaction_date']}.",
                    [str(row)],
                    sql_query,
                )

        if "premium debit" in query_lower:
            sql_query = """
                SELECT transaction_id, transaction_date, amount_usd, merchant_brand_name,
                       merchant_system_category1_name, country, channel, card_type
                FROM transactions
                WHERE customer_id = ? AND LOWER(card_type) = 'premium debit'
                ORDER BY transaction_date DESC
                LIMIT 50
            """
            cursor.execute(sql_query, (customer_id,))
            rows = cursor.fetchall()
            result_rows = _rows_as_strings(cursor, rows)
            total = sum((dict_row.get("amount_usd") or 0.0) for dict_row in [dict(zip([col[0] for col in cursor.description], row)) for row in rows])
            return (
                f"I found {len(rows)} recent Premium Debit transactions totaling ${total:,.2f}.",
                result_rows,
                sql_query,
            )

        country_match = re.search(r"\bin\s+([a-zA-Z ]+)\??$", query_text)
        if country_match and "transaction" in query_lower:
            country = country_match.group(1).strip()
            sql_query = """
                SELECT transaction_id, transaction_date, amount_usd, merchant_brand_name,
                       merchant_system_category1_name, country, channel, card_type
                FROM transactions
                WHERE customer_id = ? AND LOWER(country) = LOWER(?)
                ORDER BY transaction_date DESC
                LIMIT 50
            """
            cursor.execute(sql_query, (customer_id, country))
            rows = cursor.fetchall()
            return (
                f"I found {len(rows)} recent transactions in {country}.",
                _rows_as_strings(cursor, rows),
                sql_query,
            )

        if category and "transaction" in query_lower:
            sql_query = """
                SELECT transaction_id, transaction_date, amount_usd, merchant_brand_name,
                       merchant_system_category1_name, country, channel, card_type
                FROM transactions
                WHERE customer_id = ?
                  AND LOWER(merchant_system_category1_name) = LOWER(?)
                ORDER BY transaction_date DESC
                LIMIT 50
            """
            cursor.execute(sql_query, (customer_id, category))
            rows = cursor.fetchall()
            return (
                f"I found {len(rows)} recent {category} transactions.",
                _rows_as_strings(cursor, rows),
                sql_query,
            )

    finally:
        cursor.close()

    return None

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


@app.post("/process")
async def process(
    input_request : UIUserRequest,
    openai_llm : Annotated[Any,Depends(get_llm)], # type: ignore
    prompts : Annotated[Prompts,Depends(get_prompts)], # type: ignore
    logger: Annotated[Logger,Depends(get_logging)], # type: ignore
    redis_client: Annotated[any,Depends(get_redis_client)], # type: ignore
    db_conn: Annotated[any,Depends(get_db_conn)], # type: ignore


):
    # Preemptive bypass disabled: Ensure that all transaction queries run through the Agent Graph
    # to fetch real data dynamically from the database, using _local_transaction_fallback strictly
    # as a backup if the agent graph execution fails.
    chat_history = get_chat_history(
                redis_client, 
                input_request.customer_id, 
                input_request.session_id,
                input_request.user_query
                )
    

    logger.info(f"Chat history retrieved: {chat_history}")
    graph_state = Graph_State(
        input_request = input_request,
        chat_history = chat_history,
        customer_id = input_request.customer_id,
        context_builder_response = None,
        entity_extraction_response= None,
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
                              logger= logger,
                              db_conn=db_conn)
    agent_graph = Agent_Graph(openai_llm=openai_llm,
                              prompts=prompts,
                              logger= logger,
                              agent_subgraph=agent_subgraph)
    
    try:
        
        logger.info("Process endpoint invoked sucessfully")
        response = await agent_graph.app.ainvoke(graph_state)
        
        #response = response["context_builder_response"].user_query
        # final_chat_response = ResponseGeneratorResponse(
        #                       generated_response = str(response.response_generation_response.generated_response)
        #                     )
        final_chat_response = response["response_generation_response"].generated_response

        save_chat_history(redis_client,
                          input_request.customer_id,
                          input_request.session_id,
                          chat_history,
                          final_chat_response,
                          logger)

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

        logger.info("Final response generated")
    except Exception as e:
        logger.exception("Error while invoking agent graph")
        return JSONResponse({"error": "internal_error", "details": str(e)}, status_code=500)

    logger.info(f"Final response generated: {final_chat_response}")
    return JSONResponse({"response": final_chat_response, "visualization": viz_payload})

@app.post("/process-debug")
async def process_debug(
    input_request: UIUserRequestDebug,
    openai_llm: Annotated[Any, Depends(get_llm)],  # type: ignore
    prompts: Annotated[Prompts, Depends(get_prompts)],  # type: ignore
    logger: Annotated[Logger, Depends(get_logging)],  # type: ignore
    db_conn: Annotated[any, Depends(get_db_conn)],  # type: ignore
):
    
    # 🔥 Use chat history directly from request
    chat_history = input_request.chat_history
    input_request_debug = UIUserRequest(
        user_query=input_request.user_query,
        customer_id=input_request.customer_id,
        session_id=input_request.session_id
    )

    logger.info(f"[DEBUG] Chat history received: {chat_history}")

    graph_state = Graph_State(
        input_request=input_request_debug,
        chat_history=chat_history,
        customer_id=input_request.customer_id,
        context_builder_response=None,
        entity_extraction_response=None,
        suprivisor_response=None,
        text2sql_generator_response=None,
        database_query_results=None,
        banksql_queries=None,
        out_of_scope_queries=None,
        fallback_response=None,
        response_generation_response=None,
        agent_metrics=None
    )

    agent_subgraph = Agent_SubGraph(
        openai_llm=openai_llm,
        prompts=prompts,
        logger=logger,
        db_conn=db_conn
    )

    agent_graph = Agent_Graph(
        openai_llm=openai_llm,
        prompts=prompts,
        logger=logger,
        agent_subgraph=agent_subgraph
    )

    try:
        logger.info("[DEBUG] Invoking agent graph")

        response = await agent_graph.app.ainvoke(graph_state)

        response_gen = response.get("response_generation_response")
        generated_response = getattr(response_gen, "generated_response", None)
        if generated_response == TECHNICAL_FALLBACK_MESSAGE:
            local_response = _local_transaction_fallback(input_request_debug, db_conn)
            if local_response:
                answer, rows, sql_query = local_response
                response["database_query_results"] = rows
                response["text2sql_generator_response"] = Text2SqlGeneratorResponse(
                    user_query=input_request.user_query,
                    sql_query=sql_query.strip()
                )
                response["response_generation_response"] = ResponseGeneratorResponse(
                    generated_response=answer
                )
                response["fallback_response"] = FallbackResponse(
                    fallback_flag=False,
                    fallback_node="local_sql_fallback",
                    fallback_reason="llm_unavailable",
                    fallback_message=None
                )

        return response

    except Exception as e:
        logger.exception("[DEBUG] Error while invoking agent graph")
        return {"error": "internal_error", "details": str(e)}



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
    logger.info(f"Received voice upload request: filename={file.filename}")
    
    # Save the uploaded file temporarily
    temp_file_name = f"temp_{file.filename}"
    with open(temp_file_name, "wb") as f:
        f.write(await file.read())
        
    try:
        from openai import AsyncOpenAI
        from app_setting import AppSettings
        settings = AppSettings()
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found. Please configure it in your environment.")
            
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
# ...existing code...
