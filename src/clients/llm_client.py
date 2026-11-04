import os
from dotenv import load_dotenv
from app_setting import AppSettings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.embeddings import Embeddings
from typing import Any

# Load environment variables from .env
load_dotenv()


def _plain_text_messages(messages: list) -> list:
    """
    The agents build messages in Anthropic's format: content as a list of
    text blocks, some carrying a "cache_control" marker for prompt caching.
    Other providers (Groq, OpenAI, Gemini) reject or mishandle that, so join
    text-only block lists back into a plain string before sending.
    """
    cleaned = []
    for message in messages:
        content = getattr(message, "content", None)
        if isinstance(content, list) and content and all(
            isinstance(part, dict) and part.get("type") == "text" for part in content
        ):
            message = message.model_copy(
                update={"content": "".join(part["text"] for part in content)}
            )
        cleaned.append(message)
    return cleaned


def _with_plain_text_messages(chat_model_class: type) -> type:
    """Subclass a LangChain chat model so every call gets plain-text messages."""

    class PlainTextChatModel(chat_model_class):
        def _generate(self, messages, *args, **kwargs):
            return super()._generate(_plain_text_messages(messages), *args, **kwargs)

        async def _agenerate(self, messages, *args, **kwargs):
            return await super()._agenerate(_plain_text_messages(messages), *args, **kwargs)

    PlainTextChatModel.__name__ = chat_model_class.__name__
    return PlainTextChatModel

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
        return _with_plain_text_messages(ChatOpenAI)(
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
        return _with_plain_text_messages(ChatGoogleGenerativeAI)(
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
        return _with_plain_text_messages(ChatGroq)(
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


class LocalEmbeddings(Embeddings):
    """
    Free, offline embeddings using the all-MiniLM-L6-v2 model that ships with
    ChromaDB (run through onnxruntime, which chromadb already installs).
    No API key or credit needed. The model (~80 MB) is downloaded once on
    first use and cached under ~/.cache/chroma/onnx_models.
    Produces 384-dimensional, normalised vectors.
    """

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        self._model = ONNXMiniLM_L6_V2()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(value) for value in vector] for vector in self._model(texts)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


def get_embeddings(provider: str | None = None) -> Embeddings:
    """
    Returns the embedding model selected by EMBEDDING_PROVIDER in AppSettings
    (or the .env file): "local" (default, free and offline) or "openai".
    The same provider must be used to build the help-document index and to
    search it; rebuild the index after switching.
    """
    settings = AppSettings()
    provider = (provider or settings.EMBEDDING_PROVIDER).lower().strip()
    if provider == "local":
        return LocalEmbeddings()
    if provider == "openai":
        return get_openai_embeddings(model=settings.EMBED_MODEL)
    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER '{provider}'. Use 'local' or 'openai'."
    )
