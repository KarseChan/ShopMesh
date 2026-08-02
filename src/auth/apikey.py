"""API Key generation, hashing, and validation.

API Keys serve as an alternative authentication method for merchant integrations.
Format: sk_live_<32-random-hex>  (key_id = first 12 chars after prefix)
The full key is bcrypt-hashed before storage; only the key_id prefix is stored
for lookup and display purposes.
"""

from __future__ import annotations

import hashlib
import secrets

import bcrypt
import structlog
from datetime import datetime, timezone
from sqlmodel import Session, select

from src.db.models import ApiKey

logger = structlog.get_logger(__name__)

# ---------- Key generation ----------

_KEY_PREFIX = "sk_live_"
_KEY_ID_LENGTH = 12  # chars from the random part used as public identifier


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key.

    Returns:
        (full_key, key_id) — full_key shown once to user, key_id stored for lookup.
    """
    random_part = secrets.token_hex(24)  # 48 hex chars
    full_key = f"{_KEY_PREFIX}{random_part}"
    key_id = f"{_KEY_PREFIX}{random_part[:_KEY_ID_LENGTH]}"
    return full_key, key_id


def hash_key(full_key: str) -> str:
    """Hash the full API key with bcrypt."""
    return bcrypt.hashpw(full_key.encode(), bcrypt.gensalt()).decode()


def verify_key(full_key: str, key_hash: str) -> bool:
    """Verify a full API key against its bcrypt hash."""
    return bcrypt.checkpw(full_key.encode(), key_hash.encode())


# ---------- DB operations ----------

def create_api_key(
    db: Session,
    tenant_id: str,
    name: str = "",
) -> tuple[str, ApiKey]:
    """Create and persist a new API key for a tenant.

    Returns:
        (full_key, api_key_obj) — full_key shown once, api_key_obj is the DB record.
    """
    full_key, key_id = generate_api_key()
    key_hash = hash_key(full_key)

    api_key = ApiKey(
        key_id=key_id,
        key_hash=key_hash,
        tenant_id=tenant_id,
        name=name,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    logger.info("api_key_created", key_id=key_id, tenant_id=tenant_id, name=name)
    return full_key, api_key


def list_api_keys(db: Session, tenant_id: str) -> list[ApiKey]:
    """List all active API keys for a tenant (no secret material)."""
    return list(
        db.exec(
            select(ApiKey)
            .where(ApiKey.tenant_id == tenant_id, ApiKey.is_active == True)  # noqa: E712
            .order_by(ApiKey.created_at.desc())
        ).all()
    )


def revoke_api_key(db: Session, key_id: str, tenant_id: str) -> bool:
    """Revoke (deactivate) an API key. Returns True if found and revoked."""
    api_key = db.exec(
        select(ApiKey).where(ApiKey.key_id == key_id, ApiKey.tenant_id == tenant_id)
    ).first()
    if not api_key:
        return False
    api_key.is_active = False
    db.add(api_key)
    db.commit()
    logger.info("api_key_revoked", key_id=key_id, tenant_id=tenant_id)
    return True


def validate_api_key(db: Session, full_key: str) -> ApiKey | None:
    """Validate a full API key. Returns the ApiKey record if valid, None otherwise.

    Lookup strategy:
    1. Extract key_id from the full key
    2. Find the DB record by key_id
    3. Verify the full key against the stored bcrypt hash
    4. Check is_active
    """
    if not full_key.startswith(_KEY_PREFIX):
        return None

    key_id = full_key[: len(_KEY_PREFIX) + _KEY_ID_LENGTH]

    api_key = db.exec(
        select(ApiKey).where(ApiKey.key_id == key_id, ApiKey.is_active == True)  # noqa: E712
    ).first()
    if not api_key:
        return None

    if not verify_key(full_key, api_key.key_hash):
        return None

    # Update last_used_at (fire-and-forget, non-blocking)
    try:
        api_key.last_used_at = datetime.now(timezone.utc)
        db.add(api_key)
        db.commit()
    except Exception as e:
        # Don't fail auth on a tracking-write error, but roll back so the
        # failed transaction doesn't poison the session, and log so a
        # persistently failing write is visible instead of silent.
        db.rollback()
        logger.warning("apikey_last_used_update_failed", key_id=key_id, error=str(e))

    return api_key
