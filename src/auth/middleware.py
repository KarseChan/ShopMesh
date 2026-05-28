"""TenantContext middleware — extracts tenant_id from JWT and injects into contextvars.

For unauthenticated requests (e.g., /api/auth/*), tenant_id stays empty.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.auth.context import set_tenant_id, set_context_user_id


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract Bearer token
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

        response = await call_next(request)
        return response
