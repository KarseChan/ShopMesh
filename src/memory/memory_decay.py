"""L2c Memory Decay — scheduled cleanup of expired vector memories.

Core principle: decay is a read-time math transformation, not a write-time physical operation.
This module only does one thing: delete memories older than MAX_AGE_DAYS.

Qdrant payload is NEVER updated after write — no write amplification.
"""

import time

from src.config import config
from src.observability.logger import get_logger
from src.retrieval.vector_store import get_vector_store

logger = get_logger("memory_decay")

MAX_AGE_DAYS = config.get("memory", {}).get("max_age_days", 180)
MEMORY_COLLECTION = "user_long_term_memories"


async def cleanup_expired_all() -> int:
    """Delete all memories older than MAX_AGE_DAYS from the unified collection.

    Uses Qdrant's payload index on 'timestamp' for efficient range filtering.
    No user iteration needed — single delete_by_filter call.

    Returns number of deleted points (estimated).
    """
    store = get_vector_store()
    cutoff = time.time() - MAX_AGE_DAYS * 86400

    try:
        await store.delete_by_filter(MEMORY_COLLECTION, filters={
            "timestamp": {"range": {"lt": cutoff}},
        })
        logger.info("expired_memories_cleaned", cutoff_days=MAX_AGE_DAYS, cutoff_ts=cutoff)
        return -1  # Qdrant doesn't return count, log only
    except Exception as e:
        logger.error("cleanup_failed", error=str(e))
        return 0


async def cleanup_expired_user(user_id: str) -> int:
    """Delete expired memories for a specific user (e.g., on account deletion).

    Combines user_id equality + timestamp range in a single filter.
    """
    store = get_vector_store()
    cutoff = time.time() - MAX_AGE_DAYS * 86400

    try:
        await store.delete_by_filter(MEMORY_COLLECTION, filters={
            "user_id": user_id,
            "timestamp": {"range": {"lt": cutoff}},
        })
        logger.info("user_expired_cleaned", user_id=user_id, cutoff_days=MAX_AGE_DAYS)
        return -1
    except Exception as e:
        logger.error("user_cleanup_failed", user_id=user_id, error=str(e))
        return 0


async def cleanup_user_all(user_id: str) -> int:
    """Delete ALL memories for a user (account deletion / data purge)."""
    store = get_vector_store()

    try:
        await store.delete_by_filter(MEMORY_COLLECTION, filters={
            "user_id": user_id,
        })
        logger.info("user_memories_purged", user_id=user_id)
        return -1
    except Exception as e:
        logger.error("user_purge_failed", user_id=user_id, error=str(e))
        return 0
