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
from src.observability.logger import get_logger
from src.retrieval.vector_store import get_vector_store

logger = get_logger("semantic_router")

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
    except Exception as e:
        # Benign when the collection already exists (index rebuild); log at
        # debug so a genuine Qdrant failure during setup is not swallowed.
        logger.debug("intent_collection_ensure_skipped", collection=_COLLECTION, error=str(e))

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

    Returns (intent_dict, confidence) with multi-label support.
    Searches top-5, returns all intents above threshold as user_goals list.
    Falls back to top-1 if none exceed threshold.
    """
    embedder = get_embedder()
    store = get_vector_store()
    threshold = config.get("router", {}).get("semantic_threshold", 0.80)

    vec = await embedder.aembed(query)
    results = await store.search(_COLLECTION, vec, limit=5)

    if not results:
        return {}, 0.0

    # Collect all intents above threshold
    matched_goals = []
    best_confidence = 0.0
    best_flat_intent = ""
    for r in results:
        flat_intent = r["payload"]["intent"]
        score = r["score"]
        if score > best_confidence:
            best_confidence = score
            best_flat_intent = flat_intent
        if score >= threshold:
            mapped = _INTENT_MAP.get(flat_intent)
            if mapped and mapped["user_goal"] not in matched_goals:
                matched_goals.append(mapped["user_goal"])

    # If nothing above threshold, use top-1 as fallback (lower confidence)
    if not matched_goals and best_flat_intent:
        mapped = _INTENT_MAP.get(best_flat_intent, {})
        if mapped:
            return {
                "user_goals": [mapped["user_goal"]],
                "task_type": mapped["task_type"],
                "execution_hint": mapped["execution_hint"],
            }, best_confidence
        return {}, 0.0

    # Use the best match's task_type and execution_hint
    best_mapped = _INTENT_MAP.get(best_flat_intent, {})
    return {
        "user_goals": matched_goals,
        "task_type": best_mapped.get("task_type", "product_search"),
        "execution_hint": best_mapped.get("execution_hint", "direct_search"),
    }, best_confidence
