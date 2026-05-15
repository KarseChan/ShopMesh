"""Detail Skill — get full product details.

Read-only skill that returns complete product information by ID.
"""

from src.observability.logger import get_logger
from src.skills.schema import PermissionLevel, SkillDefinition
from src.tools.search_tool import load_products

logger = get_logger("detail_skill")

SKILL_DEFINITION = SkillDefinition(
    name="product_detail",
    description="获取商品的详细信息，包括价格、品类、描述等",
    parameters={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "商品 ID"},
        },
        "required": ["product_id"],
    },
    permissions=PermissionLevel.READ,
    version="1.0.0",
)


async def execute(product_id: str) -> dict:
    """Get product detail by ID.

    Returns:
        {"product": dict} on success, or {"error": str} if not found.
    """
    for p in load_products():
        if p.get("product_id") == product_id:
            logger.info("detail_found", product_id=product_id)
            return {"product": p}

    logger.warning("detail_not_found", product_id=product_id)
    return {"error": f"商品 {product_id} 未找到"}
