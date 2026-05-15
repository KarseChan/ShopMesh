"""Product search tool — reads from mock_data.json."""

import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "mock_data.json"


def load_products() -> list[dict]:
    with open(_DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["products"]


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
