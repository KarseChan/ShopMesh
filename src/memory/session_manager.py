"""Session Lifecycle Manager — state machine for session status transitions.

Session lifecycle:
    Created → Active → Idle → Archived → (new session on next visit)

Rules:
- Active: user is chatting, last_active_at refreshed on every turn
- Idle: no activity for idle_timeout (1h), still recoverable
- Archived: no activity for archive_timeout (24h), next visit creates new session
- New session inherits user-level long-term memory (profile + vector), NOT session-level entities
"""

import uuid
from datetime import datetime, timedelta

from src.config import config
from src.observability.logger import get_logger

logger = get_logger("session_manager")

_IDLE_TIMEOUT = config.get("session", {}).get("idle_timeout", 3600)
_ARCHIVE_TIMEOUT = config.get("session", {}).get("archive_timeout", 86400)


class SessionStatus:
    ACTIVE = "active"
    IDLE = "idle"
    ARCHIVED = "archived"


def _try_db() -> bool:
    try:
        from src.db.engine import get_session
        session = get_session()
        session.exec("SELECT 1")
        session.close()
        return True
    except Exception:
        return False


def ensure_session_sync(session_id: str, user_id: str) -> dict:
    """Ensure session is valid. Create new if expired. Synchronous.

    Returns: {"session_id": str, "is_new": bool, "prev_session_id": str | None}
    """
    if not _try_db():
        # DB unavailable — fall through, let graph handle it
        return {"session_id": session_id, "is_new": False, "prev_session_id": None}

    from src.db.engine import get_session as get_db
    from src.db.models import SessionRecord

    db = get_db()
    try:
        now = datetime.utcnow()
        record = db.exec(
            SessionRecord.select().where(SessionRecord.session_id == session_id)
        ).first()

        if record:
            elapsed = (now - record.last_active_at).total_seconds()

            if elapsed > _ARCHIVE_TIMEOUT:
                # Archive old session, create new
                record.status = SessionStatus.ARCHIVED
                db.add(record)
                db.commit()
                new_session_id = str(uuid.uuid4())
                new_record = SessionRecord(
                    session_id=new_session_id,
                    user_id=user_id,
                    status=SessionStatus.ACTIVE,
                    last_active_at=now,
                    turn_count=0,
                    last_extracted_turn=0,
                )
                db.add(new_record)
                db.commit()
                logger.info("session_archived_new_created",
                            prev=session_id[:8], new=new_session_id[:8],
                            idle_hours=round(elapsed / 3600, 1))
                return {"session_id": new_session_id, "is_new": True, "prev_session_id": session_id}

            if elapsed > _IDLE_TIMEOUT:
                # Wake from idle
                record.status = SessionStatus.ACTIVE
                record.last_active_at = now
                db.add(record)
                db.commit()
                logger.info("session_woken_from_idle", session_id=session_id[:8],
                            idle_minutes=round(elapsed / 60, 1))

            else:
                # Still active — just refresh timestamp
                record.last_active_at = now
                db.add(record)
                db.commit()

            return {"session_id": session_id, "is_new": False, "prev_session_id": None}

        # Session doesn't exist in PG — create it
        new_record = SessionRecord(
            session_id=session_id,
            user_id=user_id,
            status=SessionStatus.ACTIVE,
            last_active_at=now,
            turn_count=0,
            last_extracted_turn=0,
        )
        db.add(new_record)
        db.commit()
        logger.info("session_created", session_id=session_id[:8], user_id=user_id)
        return {"session_id": session_id, "is_new": False, "prev_session_id": None}

    except Exception as e:
        logger.error("ensure_session_error", error=str(e))
        return {"session_id": session_id, "is_new": False, "prev_session_id": None}
    finally:
        db.close()


def increment_turn_count(session_id: str) -> int:
    """Increment session turn_count in PG. Returns new count. Synchronous."""
    if not _try_db():
        return 0

    from src.db.engine import get_session as get_db
    from src.db.models import SessionRecord

    db = get_db()
    try:
        record = db.exec(
            SessionRecord.select().where(SessionRecord.session_id == session_id)
        ).first()
        if not record:
            return 0
        record.turn_count += 1
        record.last_active_at = datetime.utcnow()
        if record.status == SessionStatus.IDLE:
            record.status = SessionStatus.ACTIVE
        db.add(record)
        db.commit()
        return record.turn_count
    except Exception as e:
        logger.error("increment_turn_count_error", error=str(e))
        return 0
    finally:
        db.close()


def get_extraction_state(session_id: str) -> dict:
    """Get preference extraction state for a session. Synchronous.

    Returns: {"last_extracted_turn": int, "last_extracted_at": datetime, "extraction_count": int}
    """
    if not _try_db():
        return {"last_extracted_turn": 0, "last_extracted_at": datetime.min, "extraction_count": 0}

    from src.db.engine import get_session as get_db
    from src.db.models import PreferenceExtractionState

    db = get_db()
    try:
        state = db.exec(
            PreferenceExtractionState.select().where(
                PreferenceExtractionState.session_id == session_id
            )
        ).first()
        if state:
            return {
                "last_extracted_turn": state.last_extracted_turn,
                "last_extracted_at": state.last_extracted_at,
                "extraction_count": state.extraction_count,
            }
        return {"last_extracted_turn": 0, "last_extracted_at": datetime.min, "extraction_count": 0}
    except Exception as e:
        logger.error("get_extraction_state_error", error=str(e))
        return {"last_extracted_turn": 0, "last_extracted_at": datetime.min, "extraction_count": 0}
    finally:
        db.close()


def update_extraction_state(session_id: str, user_id: str, up_to_turn: int) -> None:
    """Update preference extraction state after a successful batch extraction. Synchronous."""
    if not _try_db():
        return

    from src.db.engine import get_session as get_db
    from src.db.models import PreferenceExtractionState

    db = get_db()
    try:
        state = db.exec(
            PreferenceExtractionState.select().where(
                PreferenceExtractionState.session_id == session_id
            )
        ).first()
        if state:
            state.last_extracted_turn = up_to_turn
            state.last_extracted_at = datetime.utcnow()
            state.extraction_count += 1
        else:
            state = PreferenceExtractionState(
                session_id=session_id,
                user_id=user_id,
                last_extracted_turn=up_to_turn,
                last_extracted_at=datetime.utcnow(),
                extraction_count=1,
            )
        db.add(state)
        db.commit()
        logger.info("extraction_state_updated", session_id=session_id[:8], up_to_turn=up_to_turn)
    except Exception as e:
        logger.error("update_extraction_state_error", error=str(e))
    finally:
        db.close()


def get_session_record(session_id: str) -> dict | None:
    """Get session record from PG. Synchronous. Returns None if not found."""
    if not _try_db():
        return None

    from src.db.engine import get_session as get_db
    from src.db.models import SessionRecord

    db = get_db()
    try:
        record = db.exec(
            SessionRecord.select().where(SessionRecord.session_id == session_id)
        ).first()
        if record:
            return {
                "session_id": record.session_id,
                "user_id": record.user_id,
                "status": record.status,
                "last_active_at": record.last_active_at,
                "turn_count": record.turn_count,
                "last_extracted_turn": record.last_extracted_turn,
                "created_at": record.created_at,
            }
        return None
    except Exception as e:
        logger.error("get_session_record_error", error=str(e))
        return None
    finally:
        db.close()
