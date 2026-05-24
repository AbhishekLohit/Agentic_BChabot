from fastapi import Depends, FastAPI, UploadFile, File
from contextlib import asynccontextmanager
from clients.llm_client import get_configured_llm, get_openai_embeddings
from app_models import (
                UIUserRequest,
                Graph_State,
                ResponseGeneratorResponse,
                ChatMessage,
                UIUserRequestDebug
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
from langchain_anthropic import ChatAnthropic
import uvicorn
import redis
import sqlite3

@asynccontextmanager
async def lifespan(app: FastAPI):

    app_settings = AppSettings()
    llm = get_configured_llm()
    llm_embeddings = get_openai_embeddings(model="text-embedding-3-small")
    prompts = get_all_prompts(app_settings)
    logger = setup_logging()
    
    redis_client = redis.Redis(
        host='localhost', port=6379, db=0, decode_responses=True
    )
    # Path to your SQLite database
    DB_PATH = app_settings.db_path

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

@app.get("/", response_class=HTMLResponse)
async def read_index():
    base_dir = os.path.dirname(__file__)
    index_path = os.path.join(base_dir, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), status_code=200)


@app.post("/process")
async def process(
    input_request : UIUserRequest,
    openai_llm : Annotated[Any,Depends(get_llm)], # type: ignore
    prompts : Annotated[Prompts,Depends(get_prompts)], # type: ignore
    logger: Annotated[Logger,Depends(get_logging)], # type: ignore
    redis_client: Annotated[any,Depends(get_redis_client)], # type: ignore
    db_conn: Annotated[any,Depends(get_db_conn)], # type: ignore


)-> str:
    # Run input guardrail layer
    from utils.guardrails import run_input_guardrail
    guardrail_response = await run_input_guardrail(input_request.user_query, openai_llm)
    if guardrail_response.guardrail_invoked:
        logger.warning(f"Guardrail triggered. Category: {guardrail_response.guardrail_invoked}, Reason: {guardrail_response.guardrail_reason}")
        reason = guardrail_response.guardrail_invoked
        if reason == "OUT_OF_SCOPE":
            return "I am configured only to assist with banking transactions, transaction history, and general account inquiries. Please ask a banking-related question."
        elif reason == "SYSTEM_VIOLATION":
            return "I'm sorry, but I cannot fulfill this request as it violates system instructions."
        elif reason == "SQL_VIOLATION":
            return "Security Alert: Direct database queries or SQL injection attempts are not permitted. Please ask a standard natural language banking question."
        elif reason == "PII_VIOLATION":
            return "Security Alert: Processing or revealing highly sensitive credentials (such as PINs, full card numbers, CVVs, or bank passwords) is strictly prohibited. Please rephrase your banking request without sensitive credentials."
        else:
            return "I am configured only to assist with banking transactions."

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
        default_ai_response = "I'm sorry, I couldn't process your request at the moment."
        save_chat_history(redis_client, 
                          input_request.customer_id, 
                          input_request.session_id, 
                          chat_history,
                          final_chat_response,
                          logger)
        logger.info("Final response generated")
    except Exception as e:
        logger.exception("Error while invoking agent graph")
        return {"error": "internal_error", "details": str(e)}

    logger.info(f"Final response generated: {final_chat_response}")
    return final_chat_response

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
    # Run the FastAPI app with uvicorn. Adjust host/port/reload as needed.
    # uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, reload_dirs=["src", "prompts"])
# ...existing code...
