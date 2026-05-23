# llm_connection.py
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
import os
from dotenv import load_dotenv
# Load the .env file
load_dotenv()

def get_openai_llm(
    model: str = "gpt-4o-mini",
    temperature: float = 0.2,
    
)-> ChatOpenAI:
    """
    Returns a configured LangChain OpenAI LLM instance.
    The API key is read from environment variable OPENAI_API_KEY.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found. Please set it in your environment.")

    # llm = init_chat_model(
    #     temperature=temperature,
    #     model=model,
    #     openai_api_key=api_key
    # )
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        temperature=temperature,
    )
    return llm

def get_claude_llm(
    model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.0,
) -> ChatAnthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not found. Please set it in your environment.")
    return ChatAnthropic(
        model=model,
        api_key=api_key,
        temperature=temperature,
    )

def get_openai_embeddings(model: str = "text-embedding-3-small"):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found. Please set it in your environment.")
    
    return OpenAIEmbeddings(
        model=model,
        openai_api_key=api_key
    )