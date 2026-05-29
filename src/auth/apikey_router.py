"""API Key management endpoints.

Endpoints:
- POST   /api/auth/api-keys:      Create a new API key
- GET    /api/auth/api-keys:      List API keys for current tenant
- DELETE /api/auth/api-keys/{key_id}: Revoke an API key
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.auth.apikey import create_api_key, list_api_keys, revoke_api_key
from src.auth.dependencies import get_current_user
from src.db.engine import get_session
from src.db.models import User

router = APIRouter(prefix="/api/auth/api-keys", tags=["api-keys"])


# ---------- Schemas ----------

class CreateApiKeyRequest(BaseModel):
    name: str = ""  # human-readable label


class ApiKeyResponse(BaseModel):
    key_id: str
    name: str
    is_active: bool
    created_at: str
    last_used_at: str | None = None


class CreateApiKeyResponse(BaseModel):
    key_id: str
    name: str
    api_key: str  # full key, shown only once
    message: str = "Save this key — it will not be shown again."


# ---------- Endpoints ----------

@router.post("", response_model=CreateApiKeyResponse, status_code=status.HTTP_201_CREATED)
def create_key(
    req: CreateApiKeyRequest,
    current_user: User = Depends(get_current_user),
):
    """Create a new API key for the current tenant.

    The full key is returned only once. Store it securely.
    """
    with get_session() as db:
        full_key, api_key_obj = create_api_key(
            db=db,
            tenant_id=current_user.tenant_id,
            name=req.name,
        )
    return CreateApiKeyResponse(
        key_id=api_key_obj.key_id,
        name=api_key_obj.name,
        api_key=full_key,
    )


@router.get("", response_model=list[ApiKeyResponse])
def list_keys(
    current_user: User = Depends(get_current_user),
):
    """List all active API keys for the current tenant."""
    with get_session() as db:
        keys = list_api_keys(db=db, tenant_id=current_user.tenant_id)
    return [
        ApiKeyResponse(
            key_id=k.key_id,
            name=k.name,
            is_active=k.is_active,
            created_at=k.created_at.isoformat(),
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
        )
        for k in keys
    ]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
):
    """Revoke (deactivate) an API key."""
    with get_session() as db:
        revoked = revoke_api_key(db=db, key_id=key_id, tenant_id=current_user.tenant_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
