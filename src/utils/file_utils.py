from logging import Logger
import yaml
from app_setting import AppSettings
from app_models import Prompts,ChatMessage
from langchain_core.prompts import PromptTemplate
import json
import os

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
        sql_correction_prompt= read_prompt_file(appsettings.sql_correction_prompt_path),
        visualization_prompt= read_prompt_file(appsettings.visualization_agent_prompt_path),
        history_summarizer_prompt= read_prompt_file(appsettings.history_summarizer_prompt_path),
        dispute_extraction_prompt= read_prompt_file(appsettings.dispute_extraction_prompt_path),
        dispute_reply_prompt= read_prompt_file(appsettings.dispute_reply_prompt_path),
        help_agent_prompt= read_prompt_file(appsettings.help_agent_prompt_path),
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
