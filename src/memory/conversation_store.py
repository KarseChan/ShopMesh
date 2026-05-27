"""Persistent conversation storage — PostgreSQL-backed chat history.

Provides durable message storage so users can see past conversations
after page refresh or Redis TTL expiry.

Complements L2a (Redis sliding window, ephemeral) with permanent storage.
"""

from datetime import datetime

from src.observability.logger import get_logger

logger = get_logger("conversation_store")

_db_available: bool | None = None


def _try_db() -> bool:
    """Check if DB is available (cached)."""
    global _db_available
    if _db_available is not None:
        return _db_available
    try:
        from src.db.engine import get_session
        session = get_session()
        session.exec("SELECT 1")
        session.close()
        _db_available = True
    except Exception:
        _db_available = False
    return _db_available


def save_message(user_id: str, conversation_id: str, role: str, content: str) -> None:
    """Persist a single message to PostgreSQL.

    Synchronous — intended to be called via asyncio.to_thread from async context.
    Silently skips if DB is unavailable (graceful degradation).
    """
    if not _try_db():
        return
    try:
        from src.db.engine import get_session
        from src.db.models import ConversationMessage
        session = get_session()
        msg = ConversationMessage(
            user_id=user_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
        session.add(msg)
        session.commit()
        session.close()
        logger.info("message_saved", user_id=user_id, conversation_id=conversation_id, role=role)
    except Exception as e:
        logger.warning("message_save_failed", error=str(e))


def get_conversations(user_id: str, limit: int = 20) -> list[dict]:
    """Get conversation list for a user, ordered by most recent activity.

    Returns list of {"conversation_id", "last_message_at", "preview"}.
    """
    if not _try_db():
        return []
    try:
        from src.db.engine import get_session
        from src.db.models import ConversationMessage
        from sqlmodel import col
        session = get_session()

        # Subquery: get latest message per conversation
        rows = session.exec(
            ConversationMessage.select()
            .where(ConversationMessage.user_id == user_id)
            .order_by(col(ConversationMessage.created_at).desc())
            .limit(limit * 10)  # over-fetch, then deduplicate
        ).all()
        session.close()

        seen = set()
        conversations = []
        for row in rows:
            if row.conversation_id in seen:
                continue
            seen.add(row.conversation_id)
            conversations.append({
                "conversation_id": row.conversation_id,
                "last_message_at": row.created_at.isoformat(),
                "preview": row.content[:80],
            })
            if len(conversations) >= limit:
                break

        return conversations
    except Exception as e:
        logger.warning("get_conversations_failed", error=str(e))
        return []


def get_messages(user_id: str, conversation_id: str, limit: int = 50) -> list[dict]:
    """Get messages for a conversation, ordered chronologically.

    Returns list of {"role", "content", "created_at"}.
    """
    if not _try_db():
        return []
    try:
        from src.db.engine import get_session
        from src.db.models import ConversationMessage
        from sqlmodel import col
        session = get_session()

        rows = session.exec(
            ConversationMessage.select()
            .where(
                ConversationMessage.user_id == user_id,
                ConversationMessage.conversation_id == conversation_id,
            )
            .order_by(col(ConversationMessage.created_at).asc())
            .limit(limit)
        ).all()
        session.close()

        return [
            {"role": r.role, "content": r.content, "created_at": r.created_at.isoformat()}
            for r in rows
        ]
    except Exception as e:
        logger.warning("get_messages_failed", error=str(e))
        return []
