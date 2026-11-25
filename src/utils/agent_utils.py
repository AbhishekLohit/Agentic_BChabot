from app_models import Metrics


def message_to_text(message) -> str:
    """Extract plain text from an AIMessage / accumulated AIMessageChunk whose
    `content` may be a string or a list of content blocks (Anthropic-style)."""
    if message is None:
        return ""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                parts.append(blk.get("text", ""))
            else:
                parts.append(str(blk))
        return "".join(parts)
    return str(content)


def record_metric(agent_metrics: dict, name: str, metric) -> str:
    """Store `metric` under `name`, suffixing _2, _3... when the name is already taken
    (an agent can run more than once in one request). Returns the key used."""
    key = name
    n = 2
    while key in agent_metrics:
        key = f"{name}_{n}"
        n += 1
    agent_metrics[key] = metric
    return key


# USD per 1M tokens: (input, cached input read, output). Matched on the model name the
# provider reports (prefix / substring, case-insensitive; first match wins).
MODEL_PRICES: list[tuple[str, tuple[float, float, float]]] = [
    ("claude-opus-5-5", (4.00, 0.20, 20.00)),
    ("claude-sonnet-5-5", (2.00, 0.10, 10.00)),
    ("claude-haiku-5-5", (0.10, 0.01, 0.50)),
    ("claude-sonnet-4-6", (3.00, 0.30, 15.00)),
    ("claude-haiku-4-5", (1.00, 0.10, 5.00)),
    ("gpt-4o-mini", (0.15, 0.075, 0.60)),
    ("llama-3.3-70b", (0.59, 0.59, 0.79)),
]


def model_prices(model_name: str | None) -> tuple[float, float, float]:
    """(input, cached, output) USD per 1M tokens for a model. Self-hosted models
    (capstone / Qwen / Ollama) use CAPSTONE_COST_PER_M_IN / _OUT, default 0 (a nominal
    rate you can set to compare against a paid API). Unknown models cost 0 -- add them
    to MODEL_PRICES rather than guessing."""
    name = (model_name or "").lower()
    for key, prices in MODEL_PRICES:
        if key in name:
            return prices
    if any(k in name for k in ("qwen", "llama3.2", "capstone")):
        from app_setting import AppSettings
        s = AppSettings()
        return (s.CAPSTONE_COST_PER_M_IN, s.CAPSTONE_COST_PER_M_IN, s.CAPSTONE_COST_PER_M_OUT)
    return (0.0, 0.0, 0.0)


def agent_metrics(
        response_message: dict,
        latency: float
)-> Metrics:
    usage = response_message.usage_metadata or {}
    model_name = (response_message.response_metadata.get("model_name") or
                  response_message.response_metadata.get("model"))
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    token_details = usage.get("input_token_details", {})
    cached_tokens = token_details.get("cache_read", 0)
    cache_creation_tokens = token_details.get("cache_creation", 0)

    # Cost: priced by the model that actually answered (see MODEL_PRICES).
    in_per_m, cached_per_m, out_per_m = model_prices(model_name)
    input_cost_per_token = in_per_m / 1_000_000
    cached_input_cost_per_token = cached_per_m / 1_000_000
    output_cost_per_token = out_per_m / 1_000_000

    # ✅ Cost WITHOUT cache (baseline)
    cost_without_cache = (
        (prompt_tokens * input_cost_per_token) +
        (completion_tokens * output_cost_per_token)
    )

    # ✅ Cost WITH cache (accurate billing)
    non_cached_tokens = max(prompt_tokens - cached_tokens, 0)

    cost_with_cache = (
        (non_cached_tokens * input_cost_per_token) +
        (cached_tokens * cached_input_cost_per_token) +
        (completion_tokens * output_cost_per_token)
    )

    return_val = Metrics(
            model_used=model_name,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation_tokens,
            latency=latency,
            model_cost_without_cache= cost_without_cache,
            model_cost_with_cache= cost_with_cache
        )
    
    return return_val