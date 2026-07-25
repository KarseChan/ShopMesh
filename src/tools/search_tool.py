"""Product search tool — reads the product catalog.

Uses the same 5k catalog that the vector index is built from
(data/mock_products_5k.json), so payload filters, product detail, reviews and
the vector index all share one consistent taxonomy (flat category +
product_type). Overridable via env PRODUCT_DATA_PATH.
"""

import json
import os
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "mock_products_5k.json"
_DATA_PATH = Path(os.environ.get("PRODUCT_DATA_PATH", str(_DEFAULT_PATH)))


def load_products() -> list[dict]:
    with open(_DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    # Support both a flat list (5k catalog) and the legacy {"products": [...]} wrapper.
    if isinstance(data, dict):
        return data.get("products", [])
    return data


def search_products(
    category: str | None = None,
    keyword: str | None = None,
    max_price: float | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search products by category, keyword, and max price."""
    products = load_products()
    results = []

    for p in products:
        if category and p["category"] != category:
            continue
        if keyword and keyword not in p["name"] and keyword not in p.get("embedding_text", ""):
            continue
        if max_price and p["price"] > max_price:
            continue
        results.append(p)

    # Sort by price ascending (hardcoded ranking for MVP)
    results.sort(key=lambda x: x["price"])
    return results[:limit]
