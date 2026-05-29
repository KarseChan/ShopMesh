"""FastAPI dependencies for authentication.

Supports two auth methods:
1. JWT Bearer token (primary) — via OAuth2PasswordBearer
2. API Key (merchant integration) — via X-API-Key header
"""

from fastapi import Depends, HTTPException, Header, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from src.auth.jwt import decode_token
from src.db.engine import get_session
from src.db.models import ApiKey, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _get_db():
    with get_session() as session:
        yield session


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(_get_db),
) -> User:
    """Extract and validate the current user from JWT access token.

    Raises 401 if token is missing or invalid.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except Exception:
        raise credentials_exception

    user = db.exec(select(User).where(User.user_id == user_id)).first()
    if user is None:
        raise credentials_exception
    return user


# ---------- API Key auth ----------

def _extract_api_key(request: Request) -> str | None:
    """Extract API key from X-API-Key header."""
    return request.headers.get("x-api-key")


def get_tenant_from_api_key(
    request: Request,
    db: Session = Depends(_get_db),
) -> str:
    """Validate X-API-Key header and return tenant_id.

    Used as a dependency for endpoints that accept API Key auth.
    Raises 401 if key is missing or invalid.
    """
    from src.auth.apikey import validate_api_key

    api_key_str = _extract_api_key(request)
    if not api_key_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )

    api_key = validate_api_key(db, api_key_str)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key",
        )
    return api_key.tenant_id


def get_current_user_any(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(_get_db),
) -> User:
    """Authenticate via JWT Bearer token OR X-API-Key header.

    Tries JWT first; falls back to API Key. Raises 401 if neither works.
    When authenticated via API Key, the corresponding User record is returned
    (looked up by tenant_id — first active user in the tenant).
    """
    # Try JWT first
    if token:
        try:
            payload = decode_token(token)
            if payload.get("type") == "access":
                user_id = payload.get("sub")
                if user_id:
                    user = db.exec(select(User).where(User.user_id == user_id)).first()
                    if user:
                        return user
        except Exception:
            pass  # fall through to API Key

    # Try API Key
    from src.auth.apikey import validate_api_key

    api_key_str = _extract_api_key(request)
    if api_key_str:
        api_key = validate_api_key(db, api_key_str)
        if api_key:
            # Return the first active user for this tenant
            user = db.exec(
                select(User).where(
                    User.tenant_id == api_key.tenant_id,
                    User.is_active == True,  # noqa: E712
                )
            ).first()
            if user:
                return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials (JWT or API Key)",
    )
