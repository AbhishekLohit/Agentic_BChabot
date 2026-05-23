from fastapi import Depends, FastAPI
from contextlib import asynccontextmanager
from clients.openai_client import get_claude_llm, get_openai_embeddings
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
from typing import Annotated
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
    llm = get_claude_llm(model="claude-sonnet-4-5-20250929", temperature=0.0)
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


@app.post("/process")
async def process(
    input_request : UIUserRequest,
    openai_llm : Annotated[ChatAnthropic,Depends(get_llm)], # type: ignore
    prompts : Annotated[Prompts,Depends(get_prompts)], # type: ignore
    logger: Annotated[Logger,Depends(get_logging)], # type: ignore
    redis_client: Annotated[any,Depends(get_redis_client)], # type: ignore
    db_conn: Annotated[any,Depends(get_db_conn)], # type: ignore


)-> str:
    
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
    openai_llm: Annotated[ChatAnthropic, Depends(get_llm)],  # type: ignore
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



if __name__ == "__main__":
    # Run the FastAPI app with uvicorn. Adjust host/port/reload as needed.
    # uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, reload_dirs=["src", "prompts"])
# ...existing code...
