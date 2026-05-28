"""L2c Vector Memory — write dialog chunks and recall via semantic search.

Uses a single unified Qdrant Collection (user_long_term_memories) with
user_id payload filtering instead of per-user collections.

Chunks contain: user_input, assistant_output, entities, intent, category, timestamp, importance.

Read-time decay: score × importance × e^(-λt) — no payload updates needed.
"""

import hashlib
import math
import time

from src.auth.context import get_tenant_id
from src.models.embedder import get_embedder
from src.observability.logger import get_logger
from src.retrieval.vector_store import get_vector_store
from src.config import config

logger = get_logger("memory_retriever")

# Trigger words that suggest the user is referencing past context
REFERENCE_TRIGGERS = ["上次", "那个", "之前", "之前看的", "上次那个", "之前那个",
                       "刚才", "前面", "记得", "提到过"]

DIMENSIONS = config.get("embedding", {}).get("default", {}).get("dimensions", 1024)
RECALL_TOP_K = config.get("memory", {}).get("recall_top_k", 5)

# Read-time decay parameters
DECAY_LAMBDA = config.get("memory", {}).get("decay_lambda", 0.001)  # decay rate per day
MIN_SCORE_THRESHOLD = config.get("memory", {}).get("min_score_threshold", 0.3)

# Unified collection for all users (replaces per-user collections)
MEMORY_COLLECTION = "user_long_term_memories"
_collection_initialized = False


def _make_chunk_id(user_id: str, timestamp: float) -> str:
    """Deterministic chunk ID for dedup."""
    raw = f"{user_id}:{timestamp}"
    return hashlib.md5(raw.encode()).hexdigest()


async def _ensure_collection() -> str:
    """Ensure the unified memory collection exists with payload indexes."""
    global _collection_initialized
    if _collection_initialized:
        return MEMORY_COLLECTION

    store = get_vector_store()
    try:
        await store.create_collection(MEMORY_COLLECTION, DIMENSIONS)
        # Create payload indexes for efficient filtering
        await store.create_payload_index(MEMORY_COLLECTION, "tenant_id", "keyword")
        await store.create_payload_index(MEMORY_COLLECTION, "user_id", "keyword")
        await store.create_payload_index(MEMORY_COLLECTION, "timestamp", "float")
        await store.create_payload_index(MEMORY_COLLECTION, "category", "keyword")
        logger.info("memory_collection_created", collection=MEMORY_COLLECTION)
    except Exception:
        pass  # Collection may already exist

    _collection_initialized = True
    return MEMORY_COLLECTION


async def write_chunk(
    user_id: str,
    user_input: str,
    assistant_output: str,
    entities: dict | None = None,
    intent: str | None = None,
    category: str | None = None,
    importance: float = 1.0,
) -> None:
    """Write a dialog chunk to the unified memory collection.

    Async — can be called fire-and-forget.
    """
    col = await _ensure_collection()
    embedder = get_embedder()

    # Build the text for embedding
    parts = [f"用户: {user_input}", f"助手: {assistant_output}"]
    if entities:
        ent_str = ", ".join(f"{k}={v}" for k, v in entities.items() if v and k not in ("ambiguous", "ambiguous_fields"))
        if ent_str:
            parts.append(f"实体: {ent_str}")
    if intent:
        parts.append(f"意图: {intent}")
    if category:
        parts.append(f"品类: {category}")

    text = "\n".join(parts)
    vector = await embedder.aembed(text)

    # Payload for filtering and display
    payload = {
        "tenant_id": get_tenant_id(),
        "user_id": user_id,
        "user_input": user_input,
        "assistant_output": assistant_output,
        "entities": entities or {},
        "intent": intent or "",
        "category": category or "",
        "timestamp": time.time(),
        "importance": importance,
        "text": text,
    }

    chunk_id = _make_chunk_id(user_id, time.time())
    store = get_vector_store()
    await store.upsert(col, [chunk_id], [vector], [payload])
    logger.info("chunk_written", user_id=user_id, category=category)


# === Contradiction detection ===

_CONTRADICTION_KEYWORDS = ["不", "不要", "不用", "不喜欢", "不爱", "换成", "改了",
                           "现在", "换", "放弃", "不再", "讨厌"]

_CONTRADICTION_BRAND_KEYWORDS = ["品牌", "牌子"]


def _is_contradictory(new_input: str, old_input: str, entities: dict) -> bool:
    """Simple contradiction detection: negation words + overlapping entity references.

    Returns True if new_input likely contradicts old_input.
    """
    has_negation = any(kw in new_input for kw in _CONTRADICTION_KEYWORDS)
    if not has_negation:
        return False

    # Check if old input mentions the same brand/product
    brand = entities.get("brand", "")
    if brand and brand in old_input:
        return True

    # Check if old input mentions the same product_type
    pt = entities.get("product_type", "")
    if pt and pt in old_input:
        return True

    # Check for general topic overlap (e.g., both mention "Nike")
    # Simple word overlap
    new_words = set(new_input)
    old_words = set(old_input)
    overlap = len(new_words & old_words)
    return overlap >= 2  # at least 2 shared characters (Chinese)


async def write_chunk_with_contradiction_awareness(
    user_id: str,
    user_input: str,
    assistant_output: str,
    entities: dict | None = None,
    intent: str | None = None,
    category: str | None = None,
    importance: float = 1.0,
) -> None:
    """Write chunk with contradiction detection.

    Before writing, recalls top-3 similar memories to check for contradictions.
    If contradiction detected, injects contrast context into the new record
    so LLM can see the preference evolution trajectory.

    Does NOT modify old records — new records naturally rank higher due to recency.
    """
    entities = entities or {}
    cat = category or entities.get("category", "")

    # Recall existing memories for contradiction check
    contradiction_context = None
    try:
        existing = await recall(user_id, user_input, category=cat or None, top_k=3)
        for mem in existing:
            if _is_contradictory(user_input, mem["user_input"], entities):
                contradiction_context = mem["user_input"][:50]
                logger.info("memory_contradiction_detected",
                            old=mem["user_input"][:50],
                            new=user_input[:50],
                            days_old=mem.get("days_old", 0))
                break
    except Exception:
        pass  # recall failure should not block write

    # Inject contrast context if contradiction found
    enhanced_input = user_input
    if contradiction_context:
        enhanced_input = f"[偏好变更] {user_input}（之前偏好: {contradiction_context}）"

    await write_chunk(
        user_id=user_id,
        user_input=enhanced_input,
        assistant_output=assistant_output,
        entities=entities,
        intent=intent,
        category=cat,
        importance=importance,
    )


def should_recall(query: str, current_category: str | None = None,
                  prev_category: str | None = None) -> bool:
    """Detect whether the query should trigger memory recall.

    Triggers:
    - Reference words in query (上次/那个/之前...)
    - Cross-category jump (user switches category)
    """
    # Check reference triggers
    for trigger in REFERENCE_TRIGGERS:
        if trigger in query:
            return True

    # Check cross-category jump
    if current_category and prev_category and current_category != prev_category:
        return True

    return False


async def should_recall_dual(
    query: str,
    user_id: str,
    current_category: str | None = None,
    prev_category: str | None = None,
    semantic_threshold: float = 0.75,
) -> tuple[bool, str]:
    """Dual-path recall: explicit triggers + implicit semantic similarity.

    Path 1 (explicit): trigger words or cross-category jump → always recall
    Path 2 (semantic): if top-1 vector match score >= threshold → recall

    Returns:
        (should_recall, reason): reason is "explicit", "semantic", or "none"
    """
    # Path 1: Explicit triggers (fast, local)
    for trigger in REFERENCE_TRIGGERS:
        if trigger in query:
            return True, "explicit"

    if current_category and prev_category and current_category != prev_category:
        return True, "explicit"

    # Path 2: Semantic recall (vector search against user's memories)
    try:
        embedder = get_embedder()
        query_vector = await embedder.aembed(query)
        store = get_vector_store()

        filters = {"user_id": user_id}
        tenant_id = get_tenant_id()
        if tenant_id:
            filters["tenant_id"] = tenant_id
        results = await store.search(MEMORY_COLLECTION, query_vector, limit=1, filters=filters)

        if results and results[0].get("score", 0) >= semantic_threshold:
            return True, "semantic"
    except Exception:
        pass  # collection may not exist yet

    return False, "none"


async def recall(
    user_id: str,
    query: str,
    category: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Recall relevant past dialog chunks via semantic search with read-time decay.

    Uses unified collection with user_id payload filter.
    Applies decay formula: final_score = base_score × importance × e^(-λ × days_elapsed)
    Over-fetches (2x) then re-ranks by decayed score to surface fresher, more important memories.

    Returns list of {"text", "score", "original_score", "user_input", "assistant_output", "category", "days_old"}.
    """
    col = MEMORY_COLLECTION
    store = get_vector_store()
    embedder = get_embedder()

    k = top_k or RECALL_TOP_K
    query_vector = await embedder.aembed(query)

    # Payload filter: tenant_id + user_id required, category optional
    tenant_id = get_tenant_id()
    filters = {"user_id": user_id}
    if tenant_id:
        filters["tenant_id"] = tenant_id
    if category:
        filters["category"] = category

    # Over-fetch to compensate for decay-induced rank changes
    results = await store.search(col, query_vector, limit=k * 2, filters=filters)

    now = time.time()
    memories = []
    for r in results:
        payload = r.get("payload", {})
        base_score = r.get("score", 0)
        importance = payload.get("importance", 1.0)
        timestamp = payload.get("timestamp", now)

        # Read-time decay: score × importance × e^(-λt)
        days_elapsed = (now - timestamp) / 86400
        decayed_score = base_score * importance * math.exp(-DECAY_LAMBDA * days_elapsed)

        if decayed_score >= MIN_SCORE_THRESHOLD:
            memories.append({
                "text": payload.get("text", ""),
                "score": round(decayed_score, 4),
                "original_score": round(base_score, 4),
                "user_input": payload.get("user_input", ""),
                "assistant_output": payload.get("assistant_output", ""),
                "category": payload.get("category", ""),
                "days_old": round(days_elapsed, 1),
                "importance": importance,
            })

    # Re-sort by decayed score (may differ from Qdrant's original ranking)
    memories.sort(key=lambda x: x["score"], reverse=True)
    memories = memories[:k]

    logger.info("recalled", user_id=user_id, count=len(memories),
                query_len=len(query), decay_applied=True)
    return memories
