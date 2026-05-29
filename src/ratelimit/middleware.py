"""FastAPI middleware for distributed rate limiting.

Reads tenant_id / user_id from the auth context (set by TenantMiddleware),
checks the Redis sliding-window limiter, and returns 429 + Retry-After on excess.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.ratelimit.limiter import RateLimiter
from src.auth.context import get_tenant_id, get_context_user_id


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply three-tier (per-user / per-tenant / global) rate limiting."""

    def __init__(self, app, limiter: RateLimiter):
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        # Skip if rate limiting is disabled
        if not self.limiter.enabled:
            return await call_next(request)

        path = request.url.path

        # Skip exempt paths
        if path in self.limiter.exempt_paths:
            return await call_next(request)

        # Read identity from context (set by TenantMiddleware upstream)
        user_id = get_context_user_id()
        tenant_id = get_tenant_id()

        result = await self.limiter.check(
            user_id=user_id,
            tenant_id=tenant_id,
            path=path,
        )

        if not result.allowed:
            retry_after = result.retry_after_seconds
            return JSONResponse(
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

        # Allowed — pass through (attach rate limit headers if useful)
        response = await call_next(request)
        if result.limit > 0:
            response.headers["X-RateLimit-Limit"] = str(result.limit)
        return response
