import os
import ssl
from pathlib import Path
from dotenv import load_dotenv
from app_setting import AppSettings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from typing import Any

# Load environment variables from .env
load_dotenv()


class CapstoneChatOpenAI(ChatOpenAI):
    """ChatOpenAI for the capstone Qwen server, with one fix for structured output.

    The server's strict-JSON mode lets Qwen pad its answer with endless blank lines.
    It writes the complete JSON fields, then pads instead of closing the object,
    and runs into the 1024-token cap. The openai SDK raises LengthFinishReasonError
    on that. Here we take the cut-off text, close the JSON, and validate it against
    the schema. A reply that still doesn't parse raises the original error as before.
    """

    def with_structured_output(self, schema=None, *, include_raw: bool = False, **kwargs):
        import openai
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda
        from langchain_core.utils.json import parse_partial_json

        inner = super().with_structured_output(schema, include_raw=include_raw, **kwargs)

        def recover(err: "openai.LengthFinishReasonError"):
            completion = err.completion
            text = (completion.choices[0].message.content or "").strip()
            data = parse_partial_json(text)
            if not isinstance(data, dict):
                raise err
            try:
                parsed = schema.model_validate(data) if hasattr(schema, "model_validate") else data
            except Exception:
                raise err
            if not include_raw:
                return parsed
            usage = completion.usage
            raw = AIMessage(
                content=text,
                usage_metadata={
                    "input_tokens": usage.prompt_tokens if usage else 0,
                    "output_tokens": usage.completion_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                },
                response_metadata={"model_name": completion.model, "finish_reason": "length"},
            )
            return {"raw": raw, "parsed": parsed, "parsing_error": None}

        def run(value, config=None):
            try:
                return inner.invoke(value, config)
            except openai.LengthFinishReasonError as err:
                return recover(err)

        async def arun(value, config=None):
            try:
                return await inner.ainvoke(value, config)
            except openai.LengthFinishReasonError as err:
                return recover(err)

        return RunnableLambda(run, afunc=arun)

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

    elif provider == "capstone":
        # IIT KGP capstone Qwen3-8B server (OpenAI-compatible API over HTTPS)
        import httpx

        # Read straight from .env (loaded above) so AppSettings needs no capstone fields.
        if model is None:
            model = os.getenv("CAPSTONE_MODEL", "Qwen/Qwen3-8B")
        api_key = os.getenv("CAPSTONE_API_KEY")
        base_url = os.getenv("CAPSTONE_BASE_URL")
        cert = os.getenv("CAPSTONE_CA_CERT")
        if not api_key:
            raise ValueError("CAPSTONE_API_KEY not found. Please set it in your .env file.")
        if not base_url:
            raise ValueError("CAPSTONE_BASE_URL not found. Set it in .env (your group's URL, ending in /v1).")
        if not cert:
            raise ValueError("CAPSTONE_CA_CERT not found. Set it in .env (full path to your .crt file).")

        cert_path = Path(cert)
        if not cert_path.is_absolute():
            cert_path = Path(__file__).resolve().parents[2] / cert_path  # relative to project root
        if not cert_path.is_file():
            raise ValueError(f"Certificate file not found: {cert_path}")

        # Keep HTTPS verification ON, trusting the group's CA certificate.
        ssl_ctx = ssl.create_default_context(cafile=str(cert_path))

        extra_body = {}
        if os.getenv("CAPSTONE_DISABLE_THINKING", "true").strip().lower() in ("1", "true", "yes"):
            extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

        return CapstoneChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=min(max_tokens, 1024),  # the server rejects max_tokens above 1024
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.Client(verify=ssl_ctx),
            http_async_client=httpx.AsyncClient(verify=ssl_ctx),
            extra_body=extra_body or None,
            # The server allows few requests at once per key (HTTP 429 when busy, e.g. a teammate
            # using the same key). More retries means the SDK waits it out (~20s) instead of failing.
            max_retries=6,
        )

    else:
        raise ValueError(
            f"Unsupported LLM provider '{provider}'. "
            "Please configure one of: 'openai', 'gemini', 'groq', 'anthropic', 'capstone'."
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
