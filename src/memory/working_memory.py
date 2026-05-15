"""L1 Working Memory — in-process state for a single graph execution.

Wraps ShoppingState with get/update/clear helpers.
Not persisted; lives only for one invocation.
"""

from typing import Any


class WorkingMemory:
    """In-process working memory backed by a plain dict."""

    def __init__(self):
        self._state: dict[str, Any] = {}

    def get(self, key: str, default=None) -> Any:
        return self._state.get(key, default)

    def update(self, updates: dict[str, Any]) -> None:
        self._state.update(updates)

    def clear(self) -> None:
        self._state.clear()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._state)
