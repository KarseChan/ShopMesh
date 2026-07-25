"""FastAPI middleware for distributed rate limiting.

Reads tenant_id / user_id from the auth context (set by TenantMiddleware),
checks the Redis sliding-window limiter, and returns 429 + Retry-After on excess.

Pure ASGI middleware (not BaseHTTPMiddleware) so it does NOT buffer streaming
responses — the /api/chat SSE stream must flush incrementally.
"""

from starlette.responses import JSONResponse

from src.ratelimit.limiter import RateLimiter
from src.auth.context import get_tenant_id, get_context_user_id


class RateLimitMiddleware:
    """Apply three-tier (per-user / per-tenant / global) rate limiting."""

    def __init__(self, app, limiter: RateLimiter):
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.limiter.enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.limiter.exempt_paths:
            await self.app(scope, receive, send)
            return

        # Identity comes from context vars set by TenantMiddleware (runs upstream)
        user_id = get_context_user_id()
        tenant_id = get_tenant_id()

        result = await self.limiter.check(user_id=user_id, tenant_id=tenant_id, path=path)

        if not result.allowed:
            retry_after = result.retry_after_seconds
            response = JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "tier": result.tier,
                    "limit": result.limit,
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                },
            )
            await response(scope, receive, send)
            return

        # Allowed — inject the limit header on response start without buffering the body
        if result.limit > 0:
            limit_bytes = str(result.limit).encode("latin-1")

            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append(
                        (b"x-ratelimit-limit", limit_bytes)
                    )
                await send(message)

            await self.app(scope, receive, send_with_header)
        else:
            await self.app(scope, receive, send)
