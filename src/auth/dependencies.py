"""FastAPI dependencies for authentication."""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from src.auth.jwt import decode_token
from src.db.engine import get_session
from src.db.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def _get_db():
    with get_session() as session:
        yield session


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(_get_db),
) -> User:
    """Extract and validate the current user from JWT access token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
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
