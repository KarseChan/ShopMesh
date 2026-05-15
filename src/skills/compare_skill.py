"""Compare Skill — side-by-side product comparison.

Read-only skill that compares two products across key dimensions.
"""

from src.observability.logger import get_logger
from src.skills.schema import PermissionLevel, SkillDefinition
from src.tools.search_tool import load_products

logger = get_logger("compare_skill")

SKILL_DEFINITION = SkillDefinition(
    name="compare_products",
    description="对比两个商品的价格、品类、品牌等信息",
    parameters={
        "type": "object",
        "properties": {
            "product_id_a": {"type": "string", "description": "商品 A 的 ID"},
            "product_id_b": {"type": "string", "description": "商品 B 的 ID"},
        },
        "required": ["product_id_a", "product_id_b"],
    },
    permissions=PermissionLevel.READ,
    version="1.0.0",
)


def _find_product(product_id: str) -> dict | None:
    for p in load_products():
        if p.get("product_id") == product_id:
            return p
    return None


def _build_comparison(a: dict, b: dict) -> dict:
    """Build comparison table between two products."""
    dimensions = []

    # Price
    pa, pb = a.get("price", 0), b.get("price", 0)
    dimensions.append({
        "dimension": "价格",
        "product_a": f"¥{pa}",
        "product_b": f"¥{pb}",
        "winner": "A" if pa < pb else "B" if pb < pa else "平",
    })

    # Category
    ca, cb = a.get("category", ""), b.get("category", "")
    dimensions.append({
        "dimension": "品类",
        "product_a": ca,
        "product_b": cb,
        "winner": "平",
    })

    # Brand
    ba = a.get("brand", a.get("name", "").split("-")[0] if "-" in a.get("name", "") else "")
    bb = b.get("brand", b.get("name", "").split("-")[0] if "-" in b.get("name", "") else "")
    dimensions.append({
        "dimension": "品牌",
        "product_a": ba or "未知",
        "product_b": bb or "未知",
        "winner": "平",
    })

    # Rating
    ra, rb = a.get("rating", 0), b.get("rating", 0)
    dimensions.append({
        "dimension": "评分",
        "product_a": f"{ra}",
        "product_b": f"{rb}",
        "winner": "A" if ra > rb else "B" if rb > ra else "平",
    })

    # Platform
    dimensions.append({
        "dimension": "平台",
        "product_a": a.get("platform_id", ""),
        "product_b": b.get("platform_id", ""),
        "winner": "平",
    })

    # Recommendation
    price_diff = abs(pa - pb)
    if price_diff < max(pa, pb) * 0.1:
        rec = f"{a.get('name', 'A')}和{b.get('name', 'B')}价格接近，关注其他维度"
    elif pa < pb:
        rec = f"{a.get('name', 'A')}价格更低，性价比更高"
    else:
        rec = f"{b.get('name', 'B')}价格更低，性价比更高"

    return {
        "title": f"{a.get('name', 'A')} vs {b.get('name', 'B')}",
        "table": dimensions,
        "recommendation": rec,
    }


async def execute(product_id_a: str, product_id_b: str) -> dict:
    """Execute product comparison.

    Returns:
        {"title": str, "table": list[dict], "recommendation": str}
        or {"error": str} if product not found.
    """
    a = _find_product(product_id_a)
    b = _find_product(product_id_b)

    if not a:
        return {"error": f"商品 {product_id_a} 未找到"}
    if not b:
        return {"error": f"商品 {product_id_b} 未找到"}

    result = _build_comparison(a, b)
    logger.info("compare_executed", product_a=product_id_a, product_b=product_id_b)
    return result
