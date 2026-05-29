"""Celery tasks for memory operations — migrated from postprocessing.py fire-and-forget tasks.

These tasks run on the 'memory' queue and handle:
- Session memory trim (LLM compression)
- Vector memory write (with contradiction detection)
- Batch preference classification
- User profile update from preferences
- Conversation message persistence
"""

import asyncio

from src.tasks.celery_app import celery_app


def _run_async(coro):
    """Run an async function from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    name="tasks.memory.trim_session",
    queue="memory",
    max_retries=3,
    default_retry_delay=5,
    soft_time_limit=30,
    time_limit=60,
)
def trim_session(session_id: str, tenant_id: str = ""):
    """Trim session sliding window — LLM compression of evicted turns."""
    from src.memory.session_memory import get_session_memory
    session_mem = get_session_memory(session_id, tenant_id=tenant_id)
    return _run_async(session_mem.trim())


@celery_app.task(
    name="tasks.memory.write_vector_memory",
    queue="memory",
    max_retries=3,
    default_retry_delay=5,
    soft_time_limit=30,
    time_limit=60,
)
def write_vector_memory(
    user_id: str,
    user_input: str,
    assistant_output: str,
    entities: dict | None = None,
    intent: str | None = None,
    category: str | None = None,
    importance: float = 1.0,
):
    """Write dialog chunk to vector memory with contradiction detection."""
    from src.memory.memory_retriever import write_chunk_with_contradiction_awareness
    _run_async(write_chunk_with_contradiction_awareness(
        user_id=user_id,
        user_input=user_input,
        assistant_output=assistant_output,
        entities=entities,
        intent=intent,
        category=category,
        importance=importance,
    ))


@celery_app.task(
    name="tasks.memory.batch_classify_preferences",
    queue="memory",
    max_retries=2,
    default_retry_delay=10,
    soft_time_limit=60,
    time_limit=90,
)
def batch_classify_preferences(session_id: str, user_id: str, category: str):
    """Batch LLM preference classification — every 3 turns."""
    from src.graph.postprocessing import _batch_classify_preferences
    _run_async(_batch_classify_preferences(session_id, user_id, category))


@celery_app.task(
    name="tasks.memory.update_profile_preference",
    queue="memory",
    max_retries=3,
    default_retry_delay=3,
    soft_time_limit=15,
    time_limit=30,
)
def update_profile_preference(
    user_id: str,
    category: str,
    preference_type: str,
    text: str,
    confidence: float,
):
    """Update L3 user profile from a confirmed preference."""
    from src.memory.user_profile import update_profile_from_preference
    _run_async(update_profile_from_preference(
        user_id=user_id,
        category=category,
        preference_type=preference_type,
        text=text,
        confidence=confidence,
    ))


@celery_app.task(
    name="tasks.memory.save_conversation_message",
    queue="memory",
    max_retries=3,
    default_retry_delay=3,
    soft_time_limit=10,
    time_limit=20,
)
def save_conversation_message(
    user_id: str,
    session_id: str,
    role: str,
    content: str,
):
    """Persist a conversation message to PostgreSQL."""
    from src.memory.conversation_store import save_message
    save_message(user_id, session_id, role, content)
