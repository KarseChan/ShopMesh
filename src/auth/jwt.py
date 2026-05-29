"""JWT token verification — validates tokens issued by Java Spring Boot.

After Phase 3.5, Python no longer issues JWT tokens. Java (Spring Boot) is the
sole JWT issuer using RSA-256 asymmetric signing. Python validates tokens using
the public key fetched from Java's JWKS endpoint.

For backward compatibility during migration, HS256 symmetric fallback is supported.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt as pyjwt

from src.config import config

_jwt_cfg = config.get("auth", {})
_SECRET_KEY = _jwt_cfg.get("secret_key", "change-me-in-production")
_ALGORITHM = _jwt_cfg.get("algorithm", "HS256")
_ACCESS_TOKEN_EXPIRE_MINUTES = _jwt_cfg.get("access_token_expire_minutes", 30)
_REFRESH_TOKEN_EXPIRE_DAYS = _jwt_cfg.get("refresh_token_expire_days", 7)


# ---------- Password hashing (unchanged) ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------- Token creation (legacy — for migration/testing only) ----------

def create_access_token(user_id: str, tenant_id: str, extra: dict | None = None) -> str:
    """Create an HS256 access token. DEPRECATED: Java is the primary issuer after Phase 3.5."""
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return pyjwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def create_refresh_token(user_id: str, tenant_id: str) -> str:
    """Create an HS256 refresh token. DEPRECATED: Java is the primary issuer after Phase 3.5."""
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return pyjwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


# ---------- Token validation (primary path — uses JWKS) ----------

def decode_token(token: str) -> dict:
    """Decode and verify a JWT token.

    Primary path: RSA verification via JWKS public key (tokens issued by Java).
    Fallback: HS256 symmetric verification (migration period / tests).
    """
    from src.auth.jwks_client import validate_jwt
    return validate_jwt(token)
