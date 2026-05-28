"""Tenant context propagation via contextvars.

Set by TenantMiddleware on each request, read by tools/memory/retrieval
without explicit parameter passing.
"""

from contextvars import ContextVar

_tenant_id: ContextVar[str] = ContextVar("tenant_id", default="")
_user_id: ContextVar[str] = ContextVar("user_id", default="")


def get_tenant_id() -> str:
    return _tenant_id.get()


def set_tenant_id(tenant_id: str) -> None:
    _tenant_id.set(tenant_id)


def get_context_user_id() -> str:
    return _user_id.get()


def set_context_user_id(user_id: str) -> None:
    _user_id.set(user_id)
