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
        from sqlalchemy import text
        from src.db.engine import get_session
        session = get_session()
        # SQLAlchemy 2.x 要求文本 SQL 用 text(),裸字符串会抛错 —— 之前这里误判
        # DB 不可用,导致所有会话读写被静默跳过(历史永远存不进/读不到)。
        session.exec(text("SELECT 1"))
        session.close()
        _db_available = True
    except Exception:
        _db_available = False
    return _db_available


def save_message(user_id: str, conversation_id: str, role: str, content: str,
                  turn_id: int = 0) -> None:
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
            turn_id=turn_id,
        )
        session.add(msg)
        session.commit()
        session.close()
        logger.info("message_saved", user_id=user_id, conversation_id=conversation_id,
                     role=role, turn_id=turn_id)
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
        from sqlmodel import col, select
        session = get_session()

        # Subquery: get latest message per conversation
        rows = session.exec(
            select(ConversationMessage)
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
        from sqlmodel import col, select
        session = get_session()

        rows = session.exec(
            select(ConversationMessage)
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


def get_recent_turns(user_id: str, conversation_id: str, limit: int = 10) -> list[dict]:
    """Get recent messages formatted for window recovery.

    Same format as SessionMemory.get_window() — [{role, content}, ...].
    limit is in messages (not turns), default 10 = 5 turns.
    """
    messages = get_messages(user_id, conversation_id, limit=limit)
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def get_turns_in_range(user_id: str, conversation_id: str,
                        from_turn: int, to_turn: int) -> list[dict]:
    """Get messages for a range of turns (inclusive). Used by batch preference extraction.

    Returns list of {"role", "content", "turn_id"} ordered by turn_id then created_at.
    Each turn has 2 messages (user + assistant).
    """
    if not _try_db():
        return []
    try:
        from src.db.engine import get_session
        from src.db.models import ConversationMessage
        from sqlmodel import col, select
        session = get_session()

        rows = session.exec(
            select(ConversationMessage)
            .where(
                ConversationMessage.user_id == user_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.turn_id >= from_turn,
                ConversationMessage.turn_id <= to_turn,
            )
            .order_by(
                col(ConversationMessage.turn_id).asc(),
                col(ConversationMessage.created_at).asc(),
            )
        ).all()
        session.close()

        return [
            {"role": r.role, "content": r.content, "turn_id": r.turn_id}
            for r in rows
        ]
    except Exception as e:
        logger.warning("get_turns_in_range_failed", error=str(e))
        return []
