from fastapi import Request

def get_llm(requests: Request) :
    return requests.app.state.llm

def get_prompts(requests: Request):
    return requests.app.state.prompts

def get_logging(requests: Request):
    return requests.app.state.logger

def get_redis_client(requests: Request):
    return requests.app.state.redis_client

def get_db_conn(requests: Request):
    return requests.app.state.sqlite_db

def get_llm_embeddings(requests: Request):
    return requests.app.state.llm_embeddings

def get_help_vector_store(requests: Request):
    return requests.app.state.help_vector_store
