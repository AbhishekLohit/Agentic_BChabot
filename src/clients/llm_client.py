import os
from dotenv import load_dotenv
from app_setting import AppSettings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from typing import Any

# Load environment variables from .env
load_dotenv()

def get_configured_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None
) -> Any:
    """
    Returns a configured LangChain chat model based on AppSettings or overridden parameters.
    Supports provider: "openai", "gemini", "groq", "anthropic".
    """
    settings = AppSettings()

    # 1. Resolve provider
    if provider is None:
        provider = settings.LLM_PROVIDER
    provider = provider.lower().strip()

    # 2. Resolve temperature & max_tokens
    if temperature is None:
        temperature = settings.TEMPERATURE
    if max_tokens is None:
        max_tokens = settings.MAX_TOKENS

    # 3. Initialize specific provider
    if provider == "openai":
        if model is None:
            model = settings.OPENAI_MODEL
        api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not found. Please set it in your environment or AppSettings.")
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key
        )

    elif provider == "gemini":
        if model is None:
            model = settings.GEMINI_MODEL
        api_key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found. Please set it in your environment or AppSettings.")
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            temperature=temperature,
            max_output_tokens=max_tokens,
            google_api_key=api_key
        )

    elif provider == "groq":
        if model is None:
            model = settings.GROQ_MODEL
        api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found. Please set it in your environment or AppSettings.")
        from langchain_groq import ChatGroq
        return ChatGroq(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            groq_api_key=api_key
        )

    elif provider == "anthropic":
        if model is None:
            model = settings.ANTHROPIC_MODEL
        api_key = settings.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not found. Please set it in your environment or AppSettings.")
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key
        )

    else:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            "Please configure one of: 'openai', 'gemini', 'groq', 'anthropic'."
        )

def get_openai_embeddings(model: str = "text-embedding-3-small") -> OpenAIEmbeddings:
    """
    Returns a configured LangChain OpenAIEmbeddings instance.
    The API key is read from environment variable OPENAI_API_KEY.
    """
    settings = AppSettings()
    api_key = settings.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found. Please set it in your environment.")
    
    return OpenAIEmbeddings(
        model=model,
        openai_api_key=api_key
    )
