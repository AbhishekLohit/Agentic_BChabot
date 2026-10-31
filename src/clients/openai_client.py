# Legacy bridge file forwarding calls to the unified llm_client.py
from clients.llm_client import get_configured_llm, get_openai_embeddings as get_new_embeddings
from typing import Any

def get_openai_llm(
    model: str = "gpt-4o-mini",
    temperature: float = 0.2,
) -> Any:
    """Legacy wrapper forwarding to the unified get_configured_llm."""
    return get_configured_llm(
        provider="openai",
        model=model,
        temperature=temperature
    )

def get_claude_llm(
    model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.0,
) -> Any:
    """Legacy wrapper forwarding to the unified get_configured_llm."""
    return get_configured_llm(
        provider="anthropic",
        model=model,
        temperature=temperature
    )

def get_openai_embeddings(model: str = "text-embedding-3-small") -> Any:
    """Legacy wrapper forwarding to the unified get_openai_embeddings."""
    return get_new_embeddings(model=model)