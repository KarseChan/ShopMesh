"""L4 Knowledge Base — product, category, and promotion knowledge.

Wraps existing product data and provides structured queries
for the context assembler.
"""

from src.tools.search_tool import load_products
from src.observability.logger import get_logger

logger = get_logger("knowledge_base")

_products_cache: list[dict] | None = None


def _get_products() -> list[dict]:
    global _products_cache
    if _products_cache is None:
        _products_cache = load_products()
    return _products_cache


def query_knowledge(entities: dict) -> dict:
    """Query knowledge base based on extracted entities.

    Returns:
        {
            "products": [...],      # Matching products (top 5)
            "promotions": [...],    # Relevant promotions
            "category_info": str,   # Category description
        }
    """
    products = _get_products()
    category = entities.get("category")
    brand = entities.get("brand")
    price_max = entities.get("price_max")

    # Filter products
    matched = []
    for p in products:
        if category and p["category"] != category:
            continue
        if brand and brand not in p["name"] and brand != p.get("brand", ""):
            continue
        if price_max and p["price"] > price_max:
            continue
        matched.append(p)

    matched.sort(key=lambda x: x["price"])
    top_products = matched[:5]

    # Gather promotions
    promo_ids = {p.get("promotion_id") for p in top_products if p.get("promotion_id")}
    all_products = _get_products()
    promotions = []
    # Load promotions from raw data
    from pathlib import Path
    import json
    data_path = Path(__file__).resolve().parent.parent.parent / "data" / "mock_data.json"
    try:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        for promo in data.get("promotions", []):
            if promo["promotion_id"] in promo_ids:
                promotions.append(promo)
    except (OSError, json.JSONDecodeError) as e:
        # Promotions are supplementary — degrade gracefully on a missing/corrupt
        # data file. Narrowed from `except Exception`: a KeyError from a
        # malformed promo entry now surfaces as the data bug it is.
        logger.warning("promotions_load_failed", data_path=str(data_path), error=str(e))

    # Category info
    category_info = ""
    if category:
        cat_products = [p for p in products if p["category"] == category]
        if cat_products:
            brands = list({p["brand"] for p in cat_products})
            prices = [p["price"] for p in cat_products]
            category_info = (
                f"{category}品类：共{len(cat_products)}个商品，"
                f"品牌有{'、'.join(brands[:5])}，"
                f"价格区间 {min(prices)}-{max(prices)} 元"
            )
        else:
            category_info = f"{category}品类：无匹配商品"

    return {
        "products": top_products,
        "promotions": promotions,
        "category_info": category_info,
    }
