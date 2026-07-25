"""TenantContext middleware — extracts tenant_id from JWT or API Key and injects into contextvars.

For unauthenticated requests (e.g., /api/auth/*), tenant_id stays empty.
Supports two auth methods:
1. JWT Bearer token (primary)
2. X-API-Key header (merchant integration)

Implemented as a PURE ASGI middleware (not BaseHTTPMiddleware): BaseHTTPMiddleware
buffers StreamingResponse bodies, which breaks SSE — the /api/chat event stream
would only reach the client after the whole response finished. A pure ASGI
middleware never touches the response body, so streaming flushes incrementally.
"""

from src.auth.context import set_tenant_id, set_context_user_id


class TenantMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # scope["headers"] is a list of (name, value) byte tuples (names lowercased)
        headers = {k: v for k, v in scope.get("headers", [])}
        auth_header = headers.get(b"authorization", b"").decode("latin-1")

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
        else:
            # Try API Key only when no Authorization header is present
            api_key = headers.get(b"x-api-key", b"").decode("latin-1")
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

        await self.app(scope, receive, send)
