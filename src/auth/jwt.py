"""JWT token generation and verification."""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from src.config import config

_jwt_cfg = config.get("auth", {})
_SECRET_KEY = _jwt_cfg.get("secret_key", "change-me-in-production")
_ALGORITHM = _jwt_cfg.get("algorithm", "HS256")
_ACCESS_TOKEN_EXPIRE_MINUTES = _jwt_cfg.get("access_token_expire_minutes", 30)
_REFRESH_TOKEN_EXPIRE_DAYS = _jwt_cfg.get("refresh_token_expire_days", 7)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str, tenant_id: str, extra: dict | None = None) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def create_refresh_token(user_id: str, tenant_id: str) -> str:
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises jwt.InvalidTokenError on failure."""
    return jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
