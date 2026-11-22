import os
from dotenv import load_dotenv
from app_setting import AppSettings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
import httpx
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
    Supports provider: "openai", "qwen", "gemini", "groq", "anthropic", "ollama", "capstone".
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

    elif provider == "qwen":
        if model is None:
            model = settings.QWEN_MODEL
        api_key = settings.QWEN_API_KEY or os.getenv("QWEN_API_KEY")
        if not api_key:
            raise ValueError("QWEN_API_KEY not found. Please set it in your environment or AppSettings.")

        max_tokens = max(1, min(max_tokens, 1024))

        ca_cert = settings.QWEN_CA_CERT or os.getenv("QWEN_CA_CERT")
        if not ca_cert:
            raise ValueError("QWEN_CA_CERT not found. Set it to the path of your Qwen CA certificate.")
        if not os.path.isfile(ca_cert):
            raise FileNotFoundError(f"QWEN_CA_CERT file does not exist: {ca_cert}")

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=settings.QWEN_API_BASE_URL,
            http_client=httpx.Client(verify=ca_cert),
            http_async_client=httpx.AsyncClient(verify=ca_cert),
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

    elif provider == "ollama":
        # Free, local model served by the Ollama app (https://ollama.com).
        # No API key needed; the model must be pulled first: `ollama pull <model>`.
        if model is None:
            model = settings.OLLAMA_MODEL
        from langchain_ollama import ChatOllama
        return _with_plain_text_messages(ChatOllama)(
            model=model,
            temperature=temperature,
            num_predict=max_tokens,
            base_url=settings.OLLAMA_BASE_URL,
        )

    elif provider == "capstone":
        # IIT KGP capstone inference API: Qwen3-8B behind an OpenAI-compatible
        # endpoint, served over HTTPS with a private CA certificate.
        if model is None:
            model = settings.CAPSTONE_MODEL
        api_key = settings.CAPSTONE_API_KEY or os.getenv("CAPSTONE_API_KEY")
        if not api_key:
            raise ValueError("CAPSTONE_API_KEY not found. Please set it in your .env file.")
        if not settings.CAPSTONE_BASE_URL:
            raise ValueError(
                "CAPSTONE_BASE_URL not set. Use your group's URL, e.g. https://103.42.50.41:1034/v1"
            )
        ca_cert = settings.CAPSTONE_CA_CERT
        if ca_cert and not os.path.isabs(ca_cert):
            # Relative paths (e.g. certs/vm04-ca.crt) are resolved from the project root,
            # so it works whether the app is started from the root or from src/.
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            ca_cert = os.path.join(project_root, ca_cert)
        if not ca_cert or not os.path.isfile(ca_cert):
            raise ValueError(
                f"CAPSTONE_CA_CERT file not found: {ca_cert!r}. Set it to the full path of your .crt file."
            )

        import httpx

        timeout = httpx.Timeout(120.0, connect=10.0)
        # Qwen3 "thinking" output would use up the small output budget and break
        # the JSON the agents expect, so it is switched off by default.
        extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}}
            if settings.CAPSTONE_DISABLE_THINKING
            else None
        )
        return _with_plain_text_messages(ChatOpenAI)(
            model=model,
            temperature=temperature,
            max_tokens=min(max_tokens, 1024),  # server maximum per call
            api_key=api_key,
            base_url=settings.CAPSTONE_BASE_URL,
            http_client=httpx.Client(verify=ca_cert, timeout=timeout),
            http_async_client=httpx.AsyncClient(verify=ca_cert, timeout=timeout),
            max_retries=4,  # retries 429 / 503 with increasing delays
            extra_body=extra_body,
        )

    else:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            "Please configure one of: 'openai', 'qwen', 'gemini', 'groq', 'anthropic', 'ollama', 'capstone'."
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


def _project_file(path: str | None) -> str | None:
    """Resolve a path from .env; relative paths are taken from the project root."""
    if path and not os.path.isabs(path):
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(project_root, path)
    return path


class CapstoneEmbeddings(Embeddings):
    """
    IIT KGP capstone embedding API: bge-small-en-v1.5 (384-dimensional vectors)
    behind an OpenAI-compatible POST /v1/embeddings, over HTTPS with a private CA.
    Sends at most 16 texts per request (the server's limit) and retries
    capacity errors (429/5xx) and dropped connections with increasing waits.
    """

    MAX_BATCH = 16

    def __init__(self, base_url: str, api_key: str, ca_cert: str, model: str = "bge-small-en-v1.5",
                 query_instruction: str = "", max_retries: int = 5, timeout: float = 60.0) -> None:
        import httpx

        self.model = model
        self.model_id = f"capstone:{model}"
        self.query_instruction = query_instruction or ""
        self._url = base_url.rstrip("/") + "/embeddings"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._client = httpx.Client(verify=ca_cert, timeout=httpx.Timeout(timeout, connect=10.0))
        self._max_retries = max_retries

    def _post(self, batch: list[str]) -> list[list[float]]:
        import time
        import httpx

        delay = 2.0
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            try:
                response = self._client.post(self._url, headers=self._headers,
                                             json={"model": self.model, "input": batch})
            except httpx.TransportError:
                if last:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and not last:
                retry_after = response.headers.get("retry-after", "")
                time.sleep(float(retry_after) if retry_after.replace(".", "", 1).isdigit() else delay)
                delay = min(delay * 2, 30.0)
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Embedding API error {response.status_code}: {response.text[:300]}")
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            if len(data) != len(batch):
                raise RuntimeError(f"Embedding API returned {len(data)} vectors for {len(batch)} texts")
            return [[float(x) for x in item["embedding"]] for item in data]
        raise RuntimeError("Embedding API: retries exhausted")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        texts = list(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.MAX_BATCH):
            vectors.extend(self._post(texts[start:start + self.MAX_BATCH]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._post([self.query_instruction + text])[0]


def get_capstone_embeddings() -> CapstoneEmbeddings:
    settings = AppSettings()
    api_key = settings.CAPSTONE_EMBED_API_KEY or os.getenv("CAPSTONE_EMBED_API_KEY")
    if not api_key:
        raise ValueError("CAPSTONE_EMBED_API_KEY not found. Please set it in your .env file.")
    ca_cert = _project_file(settings.CAPSTONE_EMBED_CA_CERT)
    if not ca_cert or not os.path.isfile(ca_cert):
        raise ValueError(f"CAPSTONE_EMBED_CA_CERT file not found: {ca_cert!r}. "
                         "Set it to the path of vm02-ca.crt (e.g. data/certs/vm02-ca.crt).")
    return CapstoneEmbeddings(settings.CAPSTONE_EMBED_BASE_URL, api_key, ca_cert,
                              model=settings.CAPSTONE_EMBED_MODEL,
                              query_instruction=settings.CAPSTONE_EMBED_QUERY_INSTRUCTION)


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
        self.model_id = "local:all-MiniLM-L6-v2"

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
    (or the .env file): "local" (default, MiniLM, free and offline), "capstone"
    (the course's bge-small-en-v1.5 API) or "openai".
    The same provider must be used to build the help-document index and to
    search it; rebuild the index after switching.
    """
    settings = AppSettings()
    provider = (provider or settings.EMBEDDING_PROVIDER).lower().strip()
    if provider == "local":
        return LocalEmbeddings()
    if provider == "capstone":
        return get_capstone_embeddings()
    if provider == "openai":
        return get_openai_embeddings(model=settings.EMBED_MODEL)
    raise ValueError(
        f"Unsupported EMBEDDING_PROVIDER '{provider}'. Use 'local', 'capstone' or 'openai'."
    )


def embedding_model_id(embeddings: Any) -> str:
    """Stable name of an embedding model, stored with the index to detect mismatches."""
    model_id = getattr(embeddings, "model_id", None)
    if model_id:
        return model_id
    model = getattr(embeddings, "model", None)
    return f"{type(embeddings).__name__}:{model}" if model else type(embeddings).__name__
