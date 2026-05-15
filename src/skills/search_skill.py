"""Search Skill — search products by category, keyword, price.

Read-only skill wrapping the existing search_tool.
"""

from src.observability.logger import get_logger
from src.skills.schema import PermissionLevel, SkillDefinition
from src.tools.search_tool import search_products

logger = get_logger("search_skill")

SKILL_DEFINITION = SkillDefinition(
    name="search_products",
    description="根据关键词、品类、价格范围搜索商品",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "搜索关键词"},
            "category": {"type": "string", "description": "商品品类，如茶饮、护肤、数码等"},
            "max_price": {"type": "number", "description": "最高价格"},
            "limit": {"type": "integer", "description": "返回数量上限", "default": 10},
        },
        "required": [],
    },
    permissions=PermissionLevel.READ,
    version="1.0.0",
)


async def execute(
    keyword: str | None = None,
    category: str | None = None,
    max_price: float | None = None,
    limit: int = 10,
) -> dict:
    """Execute product search.

    Returns:
        {"results": list[dict], "count": int, "query": dict}
    """
    results = search_products(category=category, keyword=keyword, max_price=max_price, limit=limit)

    logger.info("search_executed", keyword=keyword, category=category,
                max_price=max_price, count=len(results))

    return {
        "results": results,
        "count": len(results),
        "query": {"keyword": keyword, "category": category, "max_price": max_price},
    }
