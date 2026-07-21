"""Result Store — in-process storage for full tool results.

Stores complete product data from search tools so that:
- Agent prompt only sees slim summaries (product_id, name, price, rank_reason_text)
- Output guard can retrieve full product data by result_id for traceability checks
- Postprocessing can access full data for final response building

Scoped to a single request turn. No Redis dependency.
"""

from __future__ import annotations


class ResultStore:
    """In-process store for full tool results. Scoped to a single request turn."""

    def __init__(self):
        self._store: dict[str, list[dict]] = {}
        self._counter = 0

    def store_products(self, products: list[dict], tool_name: str = "") -> str:
        """Store full product list, return result_id."""
        self._counter += 1
        result_id = f"{tool_name}_{self._counter}"
        self._store[result_id] = products
        return result_id

    def get_products(self, result_id: str) -> list[dict]:
        """Retrieve full product list by result_id."""
        return self._store.get(result_id, [])

    def get_all_product_ids(self) -> set[str]:
        """Get all stored product IDs (for output guard)."""
        ids: set[str] = set()
        for products in self._store.values():
            for p in products:
                pid = p.get("product_id", "")
                if pid:
                    ids.add(pid)
        return ids

    def clear(self):
        """Clear all stored results."""
        self._store.clear()
        self._counter = 0


# --- Context variable pattern (like get_context_user_id) ---

_current_store: ResultStore | None = None


def set_result_store(store: ResultStore) -> None:
    """Set the result store for the current request context."""
    global _current_store
    _current_store = store


def get_result_store() -> ResultStore:
    """Get the result store for the current request context.

    Returns a new store if none has been set (defensive fallback).
    """
    global _current_store
    if _current_store is None:
        _current_store = ResultStore()
    return _current_store


def reset_result_store() -> None:
    """Reset the result store (for cleanup between requests)."""
    global _current_store
    if _current_store is not None:
        _current_store.clear()
    _current_store = None
