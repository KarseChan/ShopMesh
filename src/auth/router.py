"""Auth API endpoints: register, login, refresh."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from src.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from src.auth.dependencies import _get_db, get_current_user
from src.db.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------- Schemas ----------

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str | None = None
    tenant_name: str | None = None  # defaults to username


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserInfo(BaseModel):
    user_id: str
    username: str
    tenant_id: str
    email: str | None


# ---------- Endpoints ----------

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(_get_db)):
    """Register a new user. Creates a tenant scoped to this user."""
    existing = db.exec(select(User).where(User.username == req.username)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    tenant_id = str(uuid.uuid4())
    user = User(
        user_id=str(uuid.uuid4()),
        username=req.username,
        hashed_password=hash_password(req.password),
        email=req.email,
        tenant_id=tenant_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.user_id, tenant_id),
        refresh_token=create_refresh_token(user.user_id, tenant_id),
    )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(_get_db)):
    """Authenticate with username + password."""
    user = db.exec(select(User).where(User.username == req.username)).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return TokenResponse(
        access_token=create_access_token(user.user_id, user.tenant_id),
        refresh_token=create_refresh_token(user.user_id, user.tenant_id),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest, db: Session = Depends(_get_db)):
    """Exchange a refresh token for a new access + refresh token pair."""
    try:
        payload = decode_token(req.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user_id = payload["sub"]
    user = db.exec(select(User).where(User.user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return TokenResponse(
        access_token=create_access_token(user.user_id, user.tenant_id),
        refresh_token=create_refresh_token(user.user_id, user.tenant_id),
    )


@router.get("/me", response_model=UserInfo)
def me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user info."""
    return UserInfo(
        user_id=current_user.user_id,
        username=current_user.username,
        tenant_id=current_user.tenant_id,
        email=current_user.email,
    )
