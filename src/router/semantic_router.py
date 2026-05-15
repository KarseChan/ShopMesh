"""Semantic Router — fast intent classification via embedding similarity.

Uses Qdrant to store intent sample vectors, queries for nearest match.
Returns intent + confidence. If confidence < threshold, caller should fallback to LLM.
"""

import json
from pathlib import Path

from src.config import config
from src.models.embedder import get_embedder
from src.retrieval.vector_store import get_vector_store

_SAMPLES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "intent_samples.json"
_COLLECTION = "intent_samples"


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


async def classify(query: str) -> tuple[str, float]:
    """Classify a query using semantic similarity.

    Returns (intent, confidence). If no match exceeds threshold, intent is empty string.
    """
    embedder = get_embedder()
    store = get_vector_store()

    vec = await embedder.aembed(query)
    results = await store.search(_COLLECTION, vec, limit=1)

    if not results:
        return "", 0.0

    best = results[0]
    return best["payload"]["intent"], best["score"]
