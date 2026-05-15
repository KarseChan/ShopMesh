"""FastAPI middleware — auto-generate request_id / session_id, inject into context.

Usage:
    from src.observability.middleware import RequestContextMiddleware
    app.add_middleware(RequestContextMiddleware)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from src.observability.logger import generate_request_id, set_request_context


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-ID", generate_request_id())
        sess_id = request.headers.get("X-Session-ID", "")

        set_request_context(req_id, sess_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
