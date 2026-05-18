"""Product Detail & Price Compare Tools — for ReAct Agent multi-step comparison.

- product_detail_batch: batch fetch product details by IDs
- price_compare: structured comparison of multiple products
"""

from src.observability.logger import get_logger
from src.tools.search_tool import load_products
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("product_detail_tool")


async def product_detail_batch(product_ids: list[str]) -> list[dict]:
    """Batch fetch product details (price, rating, stock, features, etc.).

    Args:
        product_ids: List of product IDs to fetch

    Returns:
        List of product detail dicts
    """
    products = load_products()
    id_set = set(product_ids)
    matched = [p for p in products if p.get("product_id") in id_set]

    logger.info("detail_batch_fetched", requested=len(product_ids), matched=len(matched))
    return matched


async def price_compare(product_ids: list[str]) -> dict:
    """Compare prices, ratings, and key attributes across multiple products.

    Args:
        product_ids: List of product IDs to compare (2-5 recommended)

    Returns:
        {
            "products": [{product details + comparison fields}],
            "price_range": {"min": float, "max": float},
            "best_value": product_id,
        }
    """
    products = load_products()
    id_set = set(product_ids)
    matched = [p for p in products if p.get("product_id") in id_set]

    if not matched:
        return {"products": [], "price_range": {"min": 0, "max": 0}, "best_value": None}

    prices = [p.get("price", 0) for p in matched]
    price_min, price_max = min(prices), max(prices)

    # Best value: highest (rating / price) ratio
    def _value_score(p: dict) -> float:
        price = p.get("price", 1)
        rating = p.get("rating", 0)
        return rating / price if price > 0 else 0

    best = max(matched, key=_value_score)

    # Build comparison fields
    comparison = []
    for p in matched:
        comparison.append({
            **p,
            "price_diff": p.get("price", 0) - price_min,
            "rating_rank": sorted(
                [pp.get("rating", 0) for pp in matched], reverse=True
            ).index(p.get("rating", 0)) + 1,
        })

    logger.info("price_compare_done",
                product_count=len(matched),
                price_range=f"{price_min}-{price_max}",
                best_value=best.get("product_id"))

    return {
        "products": comparison,
        "price_range": {"min": price_min, "max": price_max},
        "best_value": best.get("product_id"),
    }


# Register product_detail_batch
tool_registry.register(ToolDef(
    name="product_detail_batch",
    description="批量获取商品详情（价格、评分、库存、特征等）。"
                "用于比价场景：传入多个 product_id，一次性获取所有详情。",
    parameters={
        "type": "object",
        "properties": {
            "product_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "商品 ID 列表",
            },
        },
        "required": ["product_ids"],
    },
    return_type="list[dict]",
    func=product_detail_batch,
))

# Register price_compare
tool_registry.register(ToolDef(
    name="price_compare",
    description="对比多个商品的价格、评分、评论摘要，生成结构化对比数据。"
                "返回价格区间、性价比最高的商品等信息。",
    parameters={
        "type": "object",
        "properties": {
            "product_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要对比的商品 ID 列表（建议 2-5 个）",
            },
        },
        "required": ["product_ids"],
    },
    return_type="dict",
    func=price_compare,
))
