
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from app_setting import AppSettings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# Load environment variables from .env
load_dotenv()


def get_configured_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None
) -> Any:
    """
    Returns a configured LangChain chat model.

    Supported providers:
    - openai
    - gemini
    - groq
    - anthropic
    - qwen (capstone inference API)
    """

    settings = AppSettings()

    # 1. Resolve provider
    if provider is None:
        provider = settings.LLM_PROVIDER

    provider = provider.lower().strip()

    # 2. Resolve temperature and max_tokens
    if temperature is None:
        temperature = settings.TEMPERATURE

    if max_tokens is None:
        max_tokens = settings.MAX_TOKENS

    # 3. Initialize specific provider
    if provider == "openai":
        if model is None:
            model = settings.OPENAI_MODEL

        api_key = settings.OPENAI_API_KEY or os.getenv(
            "OPENAI_API_KEY"
        )

        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY not found. "
                "Please set it in your environment or AppSettings."
            )

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key
        )

    elif provider == "gemini":
        if model is None:
            model = settings.GEMINI_MODEL

        api_key = settings.GEMINI_API_KEY or os.getenv(
            "GEMINI_API_KEY"
        )

        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY not found. "
                "Please set it in your environment or AppSettings."
            )

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

        api_key = settings.GROQ_API_KEY or os.getenv(
            "GROQ_API_KEY"
        )

        if not api_key:
            raise ValueError(
                "GROQ_API_KEY not found. "
                "Please set it in your environment or AppSettings."
            )

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

        api_key = settings.ANTHROPIC_API_KEY or os.getenv(
            "ANTHROPIC_API_KEY"
        )

        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found. "
                "Please set it in your environment or AppSettings."
            )

        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key
        )

    elif provider == "qwen":
        # Capstone Qwen3-8B inference API
        if model is None:
            model = settings.QWEN_MODEL

        api_key = settings.QWEN_API_KEY or os.getenv(
            "QWEN_API_KEY"
        )

        base_url = settings.QWEN_BASE_URL

        ca_cert_path = (
            settings.QWEN_CA_CERT_PATH
            or os.getenv("QWEN_CA_CERT_PATH")
        )

        if not api_key:
            raise ValueError(
                "QWEN_API_KEY not found. "
                "Configure it securely in your environment."
            )

        if not ca_cert_path:
            raise ValueError(
                "QWEN_CA_CERT_PATH not found. "
                "Set the path to your assigned CA certificate."
            )

        cert_file = Path(ca_cert_path).expanduser()

        if not cert_file.is_file():
            raise FileNotFoundError(
                f"Qwen CA certificate not found: {cert_file}"
            )

        # Capstone API permits at most 1024 output tokens.
        if max_tokens < 1:
            raise ValueError(
                "max_tokens must be at least 1."
            )

        max_tokens = min(max_tokens, 1024)

        # Verify TLS using the assigned CA certificate.
        # Configure both synchronous and asynchronous clients.
        sync_client = httpx.Client(
            verify=str(cert_file),
            timeout=120.0
        )

        async_client = httpx.AsyncClient(
            verify=str(cert_file),
            timeout=120.0
        )

        return ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=base_url,
            http_client=sync_client,
            http_async_client=async_client,
            max_retries=2,
            timeout=120,
        )

    else:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            "Please configure one of: "
            "'openai', 'gemini', 'groq', 'anthropic', 'qwen'."
        )


def get_openai_embeddings(
    model: str = "text-embedding-3-small"
) -> OpenAIEmbeddings:
    """
    Returns a configured LangChain OpenAIEmbeddings instance.

    The API key is read from environment variable OPENAI_API_KEY.
    """

    settings = AppSettings()

    api_key = settings.OPENAI_API_KEY or os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY not found. "
            "Please set it in your environment."
        )

    return OpenAIEmbeddings(
        model=model,
        openai_api_key=api_key
    )
