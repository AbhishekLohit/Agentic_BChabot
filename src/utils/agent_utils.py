
import faiss
import numpy as np
from typing import Dict, Any
from app_models import AgentMetrics, Metrics, Text2SqlEntityExtractionResponse

FIELD_TO_INDEX_KEY = {
    "order_items": "order_item_name",
    "restaurant_city": "restaurant_city",
    "restaurant_name": "restaurant_name",
    "cuisines": "restaurant_cuisine",
    "restaurant_types": "restaurant_type",
}


def _search_index(
        index: faiss.Index, 
        meta_list: list[str], 
        query: str,
        embedding_client:any,
        top_k: int = 3
) -> list[tuple[str, float]]:
    # compute embedding

    q_emb = embedding_client.embed_query(query)
    q_emb = np.array([q_emb], dtype="float32")
    faiss.normalize_L2(q_emb)

    similarities, indices = index.search(q_emb, top_k)
    results = []
    for j, idx in enumerate(indices[0]):
        if idx < 0:
            continue
        score = float(similarities[0][j])
        text = meta_list[idx] if idx < len(meta_list) else None
        results.append((text, score))
    return results

def find_closest_entities(
        entity_extraction_response: Text2SqlEntityExtractionResponse,
        indexes: dict[str, any], 
        metadata: dict[str, list[str]],
        llm_embedding: any,
        top_k: int = 3
) -> list[dict]:
    results = []
    for item in getattr(entity_extraction_response, "entity_extraction", []):
        user_query = item.user_query
        entity_fields = [
            ("order_items", item.order_items),
            ("restaurant_city", item.restaurant_city),
            ("restaurant_name", item.restaurant_name),
            ("cuisines", item.cuisines),
            ("restaurant_types", item.restaurant_types),
        ]

        closest_matches = {}
        for field_name, entities in entity_fields:
            key = FIELD_TO_INDEX_KEY.get(field_name)
            index = indexes.get(key) if key else None
            meta = metadata.get(key) if key else None
            field_matches = []
            if index is None or meta is None:
                # no index/metadata available for this field
                field_matches = [None for _ in entities]
            else:
                for entity in entities:
                    try:
                        matches = _search_index(index, meta, entity,llm_embedding,top_k)
                    except Exception:
                        matches = []
                    field_matches.append(matches)
            closest_matches[field_name] = field_matches

        results.append({
            "user_query": user_query,
            "entities": {name: entities for name, entities in entity_fields},
            "closest_matches": closest_matches,
        })

    return results

def agent_metrics(
        response_message: dict, 
        latency: float 
)-> Metrics:
    usage = response_message.usage_metadata or {}
    model_name = response_message.response_metadata.get("model_name")
    prompt_tokens = usage.get("input_tokens", 0)
    completion_tokens = usage.get("output_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    cached_tokens = usage.get("input_token_details", {}).get("cache_read", 0)

    # 🔥 COST CALCULATION
    # Pricing (GPT-4.1 mini)
    INPUT_COST_PER_1K = 0.0004
    CACHED_INPUT_COST_PER_1K = 0.0001
    OUTPUT_COST_PER_1K = 0.0016

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
            latency=latency,
            model_cost_without_cache= cost_without_cache,
            model_cost_with_cache= cost_with_cache
        )
    
    return return_val