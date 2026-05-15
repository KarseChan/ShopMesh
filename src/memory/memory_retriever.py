"""L2c Vector Memory — write dialog chunks and recall via semantic search.

Each user gets a dedicated Qdrant Collection: memory_{user_id}.
Chunks contain: user_input, assistant_output, entities, intent, category, timestamp.
"""

import hashlib
import time

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


def _collection_name(user_id: str) -> str:
    """Qdrant collection name for a user's memory."""
    safe = hashlib.md5(user_id.encode()).hexdigest()[:12]
    return f"memory_{safe}"


def _make_chunk_id(user_id: str, timestamp: float) -> str:
    """Deterministic chunk ID for dedup."""
    raw = f"{user_id}:{timestamp}"
    return hashlib.md5(raw.encode()).hexdigest()


async def _ensure_collection(user_id: str) -> str:
    """Ensure the user's memory collection exists in Qdrant."""
    col = _collection_name(user_id)
    store = get_vector_store()
    try:
        await store.create_collection(col, DIMENSIONS)
    except Exception:
        pass  # Collection may already exist
    return col


async def write_chunk(
    user_id: str,
    user_input: str,
    assistant_output: str,
    entities: dict | None = None,
    intent: str | None = None,
    category: str | None = None,
) -> None:
    """Write a dialog chunk to the user's vector memory.

    Async — can be called fire-and-forget.
    """
    col = await _ensure_collection(user_id)
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
        "user_id": user_id,
        "user_input": user_input,
        "assistant_output": assistant_output,
        "entities": entities or {},
        "intent": intent or "",
        "category": category or "",
        "timestamp": time.time(),
        "text": text,
    }

    chunk_id = _make_chunk_id(user_id, time.time())
    store = get_vector_store()
    await store.upsert(col, [chunk_id], [vector], [payload])
    logger.info("chunk_written", user_id=user_id, category=category)


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


async def recall(
    user_id: str,
    query: str,
    category: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Recall relevant past dialog chunks via semantic search.

    Returns list of {"text", "score", "user_input", "assistant_output", "category"}.
    """
    col = _collection_name(user_id)
    store = get_vector_store()
    embedder = get_embedder()

    k = top_k or RECALL_TOP_K
    query_vector = await embedder.aembed(query)

    # Build filter if category specified
    filters = {"user_id": user_id}
    if category:
        filters["category"] = category

    results = await store.search(col, query_vector, limit=k, filters=filters)

    memories = []
    for r in results:
        memories.append({
            "text": r.get("payload", {}).get("text", ""),
            "score": r.get("score", 0),
            "user_input": r.get("payload", {}).get("user_input", ""),
            "assistant_output": r.get("payload", {}).get("assistant_output", ""),
            "category": r.get("payload", {}).get("category", ""),
        })

    logger.info("recalled", user_id=user_id, count=len(memories), query_len=len(query))
    return memories
