"""JWKS (JSON Web Key Set) client — fetches RSA public key from Java's JWKS endpoint.

On startup, pulls the public key from /.well-known/jwks.json and caches it.
Periodically refreshes the key to support key rotation.

Usage:
    from src.auth.jwks_client import get_public_key, validate_jwt
    claims = validate_jwt(token)
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx
import jwt
import structlog

from src.config import config

logger = structlog.get_logger(__name__)

_jwks_url: str = ""
_cached_key: Any = None
_cached_kid: str = ""
_last_fetch: float = 0
_refresh_interval: int = 3600  # 1 hour
_lock = threading.Lock()


def _b64url_to_int(value: str) -> int:
    """Decode a base64url-encoded string to an integer."""
    import base64
    # Add padding
    padding = 4 - len(value) % 4
    if padding != 4:
        value += "=" * padding
    return int.from_bytes(base64.urlsafe_b64decode(value), "big")


def _jwk_to_rsa_public_key(jwk: dict) -> Any:
    """Convert a JWK (RSA) to a cryptography RSA public key."""
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
    from cryptography.hazmat.backends import default_backend

    n = _b64url_to_int(jwk["n"])
    e = _b64url_to_int(jwk["e"])
    public_numbers = RSAPublicNumbers(e, n)
    return public_numbers.public_key(default_backend())


def _fetch_jwks() -> tuple[Any, str] | None:
    """Fetch the JWKS endpoint and return (public_key, kid)."""
    global _jwks_url
    if not _jwks_url:
        auth_cfg = config.get("auth", {})
        _jwks_url = auth_cfg.get("jwks_url", "")

    if not _jwks_url:
        logger.warning("jwks_url_not_configured")
        return None

    try:
        resp = httpx.get(_jwks_url, timeout=5.0)
        resp.raise_for_status()
        jwks = resp.json()

        keys = jwks.get("keys", [])
        if not keys:
            logger.error("jwks_no_keys_found")
            return None

        # Use the first RSA key
        for key in keys:
            if key.get("kty") == "RSA":
                public_key = _jwk_to_rsa_public_key(key)
                kid = key.get("kid", "")
                logger.info("jwks_key_fetched", kid=kid, url=_jwks_url)
                return public_key, kid

        logger.error("jwks_no_rsa_key_found")
        return None

    except Exception as e:
        logger.error("jwks_fetch_failed", error=str(e), url=_jwks_url)
        return None


def get_public_key() -> Any:
    """Get the cached RSA public key, fetching if needed."""
    global _cached_key, _cached_kid, _last_fetch

    now = time.time()
    if _cached_key and (now - _last_fetch) < _refresh_interval:
        return _cached_key

    with _lock:
        # Double-check after acquiring lock
        if _cached_key and (now - _last_fetch) < _refresh_interval:
            return _cached_key

        result = _fetch_jwks()
        if result:
            _cached_key, _cached_kid = result
            _last_fetch = now
            return _cached_key

        # If fetch failed but we have a cached key, keep using it
        if _cached_key:
            logger.warning("jwks_refresh_failed_using_cached", kid=_cached_kid)
            return _cached_key

        return None


def validate_jwt(token: str) -> dict:
    """Validate a JWT using the RSA public key from JWKS.

    Falls back to HS256 symmetric verification if JWKS is not available
    (migration period only).

    Returns the decoded claims dict. Raises jwt.InvalidTokenError on failure.
    """
    public_key = get_public_key()

    if public_key:
        # RS256 verification with JWKS public key
        return jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_exp": True},
        )
    else:
        # Fallback: HS256 symmetric (migration period)
        auth_cfg = config.get("auth", {})
        secret = auth_cfg.get("secret_key", "")
        if not secret:
            raise jwt.InvalidTokenError("No JWKS key available and no symmetric secret configured")
        logger.warning("jwt_using_hs256_fallback")
        return jwt.decode(token, secret, algorithms=["HS256"])


def refresh_key() -> None:
    """Force-refresh the cached JWKS key (e.g., on key rotation detection)."""
    global _cached_key, _last_fetch
    with _lock:
        _cached_key = None
        _last_fetch = 0
    get_public_key()
