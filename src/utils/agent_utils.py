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

    # 🔥 COST CALCULATION
    # Pricing (Claude Haiku 4.5)
    INPUT_COST_PER_1K = 0.0008
    CACHED_INPUT_COST_PER_1K = 0.00008
    OUTPUT_COST_PER_1K = 0.004

    input_cost_per_token = INPUT_COST_PER_1K / 1000
    cached_input_cost_per_token = CACHED_INPUT_COST_PER_1K / 1000
    output_cost_per_token = OUTPUT_COST_PER_1K / 1000

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