"""Review Summary Tool — extract key selling points and concerns from product data.

Since mock data has features/reputation but no raw review text,
this tool synthesizes a review-style summary from available product attributes.
In production, this would call a real review API or LLM summarizer.
"""

from src.observability.logger import get_logger
from src.tools.search_tool import load_products
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("review_tool")

# Sentiment mapping for reputation score
_REPUTATION_LABELS = [
    (0.9, "好评如潮"),
    (0.75, "口碑不错"),
    (0.6, "评价一般"),
    (0.0, "评价较少"),
]


def _reputation_label(score: float) -> str:
    for threshold, label in _REPUTATION_LABELS:
        if score >= threshold:
            return label
    return "评价较少"


async def review_summary(
    product_ids: list[str],
    aspects: list[str] | None = None,
) -> list[dict]:
    """Batch extract key selling points and concerns for products.

    Args:
        product_ids: List of product IDs
        aspects: Optional aspect filter (e.g. ["质量", "性价比", "舒适度"]).
                 Currently unused; reserved for LLM-based aspect extraction.

    Returns:
        List of {"product_id", "name", "reputation_label", "selling_points", "concerns"}
    """
    products = load_products()
    id_set = set(product_ids)
    by_id = {p["product_id"]: p for p in products if p.get("product_id") in id_set}

    # Preserve input order
    matched = [by_id[pid] for pid in product_ids if pid in by_id]

    results = []
    for p in matched:
        features = p.get("features", [])
        reputation = p.get("reputation", 0)

        # Derive selling points from features
        selling_points = features[:4] if features else ["暂无特征数据"]

        # Derive concerns from low reputation or missing features
        concerns = []
        if reputation < 0.6:
            concerns.append("口碑评分偏低")
        if not features:
            concerns.append("缺少详细特征描述")
        if p.get("stock", 0) < 10:
            concerns.append("库存较少，可能缺货")

        results.append({
            "product_id": p.get("product_id"),
            "name": p.get("name"),
            "reputation": reputation,
            "reputation_label": _reputation_label(reputation),
            "selling_points": selling_points,
            "concerns": concerns if concerns else ["暂无明显槽点"],
        })

    logger.info("review_summary_done", requested=len(product_ids), matched=len(results))
    return results


# Register
tool_registry.register(ToolDef(
    name="review_summary",
    description="批量提取商品评论的关键卖点和槽点摘要。"
                "传入商品 ID 列表，返回每个商品的口碑标签、卖点和注意事项。",
    parameters={
        "type": "object",
        "properties": {
            "product_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "商品 ID 列表",
            },
            "aspects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选，关注的方面（如['质量','性价比']），当前预留",
            },
        },
        "required": ["product_ids"],
    },
    return_type="list[dict]",
    func=review_summary,
))
