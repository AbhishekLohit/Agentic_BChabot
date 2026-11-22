from logging import Logger
import sqlite3
import yaml
from app_setting import AppSettings
from app_models import Prompts,ChatMessage
from langchain_core.prompts import PromptTemplate
import json
from misc.get_db_info import db_info
import os
from langchain_openai import OpenAIEmbeddings
import pandas as pd
import numpy as np
import faiss

def read_prompt_file(prompt:str
) -> PromptTemplate:
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # Go up from src/utils to project root
    prompt_path = os.path.join(base_dir, 'prompts', prompt)
    
    with open(prompt_path, 'r',encoding="utf-8") as file:
        prompt_data = yaml.safe_load(file)
        return (PromptTemplate.from_template(prompt_data['prompt']))


def get_all_prompts(appsettings:AppSettings
) -> Prompts:
    return Prompts(
        context_builder_prompt = read_prompt_file(appsettings.context_builder_prompt_path),
        suprivisor_prompt = read_prompt_file(appsettings.suprivisor_prompt_path),
        text2sqlgenerator_prompt = read_prompt_file(appsettings.text2sql_generator_prompt_path),
        responsegenerator_prompt= read_prompt_file(appsettings.response_generator_prompt_path),
        fallback_prompt= read_prompt_file(appsettings.fallback_prompt_path),
        sql_correction_prompt= read_prompt_file(appsettings.sql_correction_prompt_path),
        test_data_generation_prompt= read_prompt_file(appsettings.test_data_generation_prompt_path),
        visualization_prompt= read_prompt_file(appsettings.visualization_agent_prompt_path),
        history_summarizer_prompt= read_prompt_file(appsettings.history_summarizer_prompt_path),
        help_center_prompt= read_prompt_file(appsettings.help_center_prompt_path),
    )

def _get_redis_key(user_id: str, 
                   session_id: str
) -> str:
    return f"chat:{user_id}:{session_id}"

def save_chat_history(
        redis_client : any,
        user_id: str,
        session_id: str,
        chat_history: list[ChatMessage],
        ai_response:str,
        logger:Logger,
        ttl_seconds: int = 1800
):
    """
    Save chat history as JSON list of dicts.
    """
    if redis_client is None:
        return
    key = _get_redis_key(user_id, session_id)
    # Convert Pydantic models -> dicts -> JSON string
    if ai_response:
        chat_history.append(ChatMessage(role="assistant", content=ai_response))
    logger.info(f"Saving chat history to Redis key: {chat_history}")
    serialized = json.dumps([msg.dict() for msg in chat_history])
    try:
        redis_client.set(key, serialized, ex=ttl_seconds)
    except Exception as exc:
        logger.warning("Redis unavailable while saving chat history; continuing without persistence: %s", exc)

def get_chat_history(
        redis_client:any,
        user_id: str,
        session_id: str,
        user_latest_message: str
) -> list[ChatMessage]:
    """
    Retrieve chat history and convert JSON -> list of ChatMessage
    """
    if redis_client is None:
        return [ChatMessage(role="user", content=user_latest_message)]
    history : list[ChatMessage] =  []
    key = _get_redis_key(user_id, session_id)
    try:
        history_json = redis_client.get(key)
        if history_json:
            data = json.loads(history_json)
            history =  [ChatMessage(**msg) for msg in data]  # Convert dicts back to ChatMessage objects
    except Exception:
        history = []
    history.append(ChatMessage(role="user", content=user_latest_message))
    return history


def save_raw_chat_history(
    redis_client: any,
    user_id: str,
    session_id: str,
    history: list,
    ttl_seconds: int = 1800,
) -> None:
    if redis_client is None:
        return
    key = _get_redis_key(user_id, session_id)
    try:
        redis_client.set(key, json.dumps([m.dict() for m in history]), ex=ttl_seconds)
    except Exception:
        pass


def get_vectordb_data(
        df_conn: sqlite3.Connection,
        customer_id: str,
        logger: Logger,
        app_settings: AppSettings,
        llm_embeddings:OpenAIEmbeddings
) -> dict[str, any]:
    # export user related data from DB

    vectordb_data  = {}
    queries = {
        "merchant_name": app_settings.merchant_name_query,
        "merchant_category1": app_settings.merchant_category1_query,
        "merchant_category2": app_settings.merchant_category2_query,
        "transaction_country": app_settings.transaction_country_query,
        "transaction_channel": app_settings.transaction_channel_query,
    }
    # queries = {
    #     "restaurant_type": app_settings.restaurant_type_query,
    # }

    for entity_name, query in queries.items():
        logger.info(f"Fetching {entity_name} for customer_id={customer_id}")

        rows = db_info.execute_query(
            query,
            df_conn,
            customer_id,
            logger
        )

        # Flatten [(value,), (value,)] → [value, value]
        values = [list(row.values())[0] for row in rows if list(row.values())[0] is not None]
        if not values:
            logger.warning(f"No data found for entity: {entity_name}")
            continue

        # -----------------------------
        # Embed text list (embed_text_list logic)
        # -----------------------------
        vectors = llm_embeddings.embed_documents(values)
        vectors = np.array(vectors, dtype="float32")
        faiss.normalize_L2(vectors)

        dim = vectors.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(vectors)
        
        vectordb_data[entity_name] = {
            "values": values,
            "index": index
        }
        
        logger.info(
            f"Built FAISS index for {entity_name} "
            f"(count={len(values)}, dim={dim})"
        )
    return vectordb_data





# def get_vectordb_data() -> tuple[dict[str, any], dict[str, list[str]]]:

#     # --- Load vector indexes and metadata ---
#     index_dir = "src/utils/embeddings/data/index"
#     meta_dir = "src/utils/embeddings/data/metadata"
#     index_files = {
#         "order_item_name": "order_item_name_index.index",
#         "restaurant_name": "restaurant_name_index.index",
#         "restaurant_city": "restaurant_city_index.index",
#         "restaurant_type": "restaurant_type_index.index",
#         "restaurant_cuisine": "restaurant_cuisine_index.index"
#     }
#     meta_files = {
#         "order_item_name": "order_item_name_meta.csv",
#         "restaurant_name": "restaurant_name_meta.csv",
#         "restaurant_city": "restaurant_city_meta.csv",
#         "restaurant_type": "restaurant_type_meta.csv",
#         "restaurant_cuisine": "restaurant_cuisine_meta.csv"
#     }

#     vector_indexes = {}
#     vector_metadata = {}

#     for key, fname in index_files.items():
#         index_path = f"{index_dir}/{fname}"
#         meta_path = f"{meta_dir}/{meta_files[key]}"
#         vector_indexes[key] = faiss.read_index(index_path)
#         df = pd.read_csv(meta_path)
#         # Store as list of strings for fast lookup
#         vector_metadata[key] = df[df.columns[0]].tolist()

#     return vector_indexes, vector_metadata


# def get_customer_order_info(
#         user_id: str, 
#         db_conn: any
# ) -> list[ChatMessage]:
#     """
#     Checks Redis cache for customer data for this session.
#     If not found, fetches from DB and caches it.
#     """
#     # Fetch from SQLite
#     print("🗄️ Fetching from SQLite...")
#     customer_details = db_info.get_customer_details(user_id,
#                                      db_conn)

#     return customer_details
