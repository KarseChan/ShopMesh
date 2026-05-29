"""TenantContext middleware — extracts tenant_id from JWT or API Key and injects into contextvars.

For unauthenticated requests (e.g., /api/auth/*), tenant_id stays empty.
Supports two auth methods:
1. JWT Bearer token (primary)
2. X-API-Key header (merchant integration)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.auth.context import set_tenant_id, set_context_user_id


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Try JWT Bearer token first
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                from src.auth.jwt import decode_token
                payload = decode_token(token)
                if payload.get("type") == "access":
                    set_tenant_id(payload.get("tenant_id", ""))
                    set_context_user_id(payload.get("sub", ""))
            except Exception:
                pass  # Invalid token — leave context empty

        # Try API Key if JWT didn't set tenant
        if not request.headers.get("authorization"):
            api_key = request.headers.get("x-api-key")
            if api_key:
                try:
                    from src.auth.apikey import validate_api_key
                    from src.db.engine import get_session
                    with get_session() as db:
                        key_obj = validate_api_key(db, api_key)
                        if key_obj:
                            set_tenant_id(key_obj.tenant_id)
                except Exception:
                    pass  # Invalid key — leave context empty

        response = await call_next(request)
        return response
