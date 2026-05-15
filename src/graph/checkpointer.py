"""Pluggable Checkpointer for LangGraph state persistence.

MVP: MemorySaver (in-memory, for development/debug)
Pluggable design: supports memory backend, extensible to SQLite/Redis.
"""

from langgraph.checkpoint.memory import MemorySaver

from src.observability.logger import get_logger

logger = get_logger("checkpointer")

_backend: str = "memory"
_saver: MemorySaver | None = None


def get_checkpointer(backend: str = "memory") -> MemorySaver:
    """Get or create the checkpointer instance.

    Args:
        backend: "memory" (MVP), future: "sqlite", "redis"

    Returns:
        A LangGraph-compatible checkpointer.
    """
    global _saver, _backend

    if _saver is not None and _backend == backend:
        return _saver

    _backend = backend

    if backend == "memory":
        _saver = MemorySaver()
        logger.info("checkpointer_created", backend="memory")
    else:
        raise ValueError(f"Unsupported checkpointer backend: {backend}. Use 'memory'.")

    return _saver


def clear_checkpointer() -> None:
    """Clear the checkpointer singleton (for testing)."""
    global _saver
    _saver = None
