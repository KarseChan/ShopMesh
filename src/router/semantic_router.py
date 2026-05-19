"""Semantic Router — fast intent classification via embedding similarity.

Uses Qdrant to store intent sample vectors, queries for nearest match.
Returns intent_dict + confidence. If confidence < threshold, caller should fallback to LLM.

Three-layer intent structure:
- user_goal: recommend_product, find_product, compare_products, view_detail, place_order
- task_type: shopping_advice, product_search, outfit_planning, price_comparison, detail_inquiry, order_placement
- execution_hint: contextual_search, direct_search, multi_query, compare, get_detail, clarify_first
"""

import json
from pathlib import Path

from src.config import config
from src.models.embedder import get_embedder
from src.retrieval.vector_store import get_vector_store

_SAMPLES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "intent_samples.json"
_COLLECTION = "intent_samples"

# Mapping from old flat intents to new three-layer structure
_INTENT_MAP = {
    "search": {
        "user_goal": "find_product",
        "task_type": "product_search",
        "execution_hint": "direct_search",
    },
    "recommend": {
        "user_goal": "recommend_product",
        "task_type": "shopping_advice",
        "execution_hint": "contextual_search",
    },
    "compare": {
        "user_goal": "compare_products",
        "task_type": "price_comparison",
        "execution_hint": "compare",
    },
    "detail": {
        "user_goal": "view_detail",
        "task_type": "detail_inquiry",
        "execution_hint": "get_detail",
    },
    "order": {
        "user_goal": "place_order",
        "task_type": "order_placement",
        "execution_hint": "direct_search",
    },
}


async def build_intent_index():
    """Index all intent samples into Qdrant (run once)."""
    with open(_SAMPLES_PATH, encoding="utf-8") as f:
        data = json.load(f)

    embedder = get_embedder()
    store = get_vector_store()
    dim = config["embedding"]["default"]["dimensions"]

    try:
        await store.create_collection(_COLLECTION, dim)
    except Exception:
        pass

    texts, ids, payloads = [], [], []
    idx = 1
    for intent, info in data["intents"].items():
        for sample in info["samples"]:
            texts.append(sample)
            ids.append(idx)
            payloads.append({"intent": intent, "text": sample})
            idx += 1

    vectors = await embedder.aembed_batch(texts)
    await store.upsert(_COLLECTION, ids, vectors, payloads)
    return len(texts)


async def classify(query: str) -> tuple[dict, float]:
    """Classify a query using semantic similarity.

    Returns (intent_dict, confidence). If no match exceeds threshold, intent_dict is empty.
    """
    embedder = get_embedder()
    store = get_vector_store()

    vec = await embedder.aembed(query)
    results = await store.search(_COLLECTION, vec, limit=1)

    if not results:
        return {}, 0.0

    best = results[0]
    flat_intent = best["payload"]["intent"]
    confidence = best["score"]

    # Map flat intent to three-layer structure
    intent_dict = _INTENT_MAP.get(flat_intent, {})
    if not intent_dict:
        return {}, 0.0

    return intent_dict, confidence
