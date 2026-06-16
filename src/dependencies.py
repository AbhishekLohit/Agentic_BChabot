from fastapi import Depends, FastAPI, Request

def get_llm(requests: Request) :
    return requests.app.state.llm

def get_prompts(requests: Request):
    return requests.app.state.prompts

def get_logging(requests: Request):
    return requests.app.state.logger

def get_db_conn(requests: Request):
    return requests.app.state.sqlite_db

def get_user_data(requests: Request):
    return requests.app.state.user_data

def get_llm_embeddings(requests: Request):
    return requests.app.state.llm_embeddings