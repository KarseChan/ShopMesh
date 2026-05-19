"""Explainer — generate personalized recommendation reasons.

Features:
- Structured template: 因为你[偏好匹配] + 这款[商品优势] + [价格/促销信息]
- LLM polish: natural language generation
- Comparative recommendation: when two candidates are close, generate comparison card
- Counterfactual explanation: answer "why didn't you recommend X"
"""

from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("explainer")

# Dimension names for display
DIM_NAMES = {
    "relevance": "与你需求匹配",
    "price": "价格优势",
    "reputation": "口碑好",
    "timeliness": "配送快",
    "personalization": "符合你偏好",
}

PLATFORM_NAMES = {
    "jd": "京东",
    "tb": "淘宝",
    "pdd": "拼多多",
}


def _top_reasons(rank_reasons: dict, n: int = 2) -> list[str]:
    """Get top N ranking dimensions as human-readable reasons."""
    sorted_dims = sorted(rank_reasons.items(), key=lambda x: x[1], reverse=True)
    return [DIM_NAMES.get(dim, dim) for dim, _ in sorted_dims[:n]]


def _price_text(product: dict) -> str:
    """Format price with promotion info."""
    price = product.get("price", 0)
    promo_desc = product.get("promo_desc")
    if promo_desc:
        return f"¥{price}（{promo_desc}）"
    return f"¥{price}"


def generate_reason(product: dict, user_profile: dict | None = None) -> str:
    """Generate a structured recommendation reason.

    Template: 因为你[偏好匹配]，这款[商品优势]，[价格信息]
    """
    reasons = _top_reasons(product.get("rank_reasons", {}))
    price_text = _price_text(product)
    name = product.get("name", "商品")

    # Build structured reason
    parts = []

    # Platform
    platform_id = product.get("platform_id", "")
    platform_name = PLATFORM_NAMES.get(platform_id, "")
    if platform_name:
        parts.append(platform_name)

    # Preference match
    if user_profile:
        brands = user_profile.get("preferred_brands", [])
        if brands and product.get("brand") in brands:
            parts.append(f"因为你之前喜欢{product['brand']}品牌")

    # Product advantages
    if reasons:
        parts.append(f"这款{reasons[0]}")

    # Price info
    parts.append(price_text)

    # Promotion
    if product.get("suggest_message"):
        parts.append(product["suggest_message"])

    return "，".join(parts) if parts else f"推荐 {name} {price_text}"


async def polish_reason(
    product: dict, structured_reason: str, user_profile: dict | None = None
) -> str:
    """Use LLM to polish the structured reason into natural language.

    Falls back to structured reason on LLM failure.
    """
    llm = get_llm()

    name = product.get("name", "商品")
    features = ", ".join(product.get("features", []))

    prompt = (
        f"请将以下推荐理由润色为自然、亲切的中文，不超过50字：\n"
        f"商品：{name}\n"
        f"特点：{features}\n"
        f"原始理由：{structured_reason}\n"
        "只输出润色后的理由，不要其他文字。"
    )

    try:
        result = await llm.chat_json([
            {"role": "system", "content": "你是推荐理由润色助手。输出 JSON: {\"reason\": \"润色后的理由\"}"},
            {"role": "user", "content": prompt},
        ])
        polished = result.get("reason", "")
        if polished:
            logger.info("reason_polished", product=name)
            return polished
    except Exception as e:
        logger.warning("polish_failed", error=str(e))

    return structured_reason


def generate_comparison(
    product_a: dict, product_b: dict, user_profile: dict | None = None
) -> dict:
    """Generate a comparison card when two candidates are close in score.

    Returns:
        {"title": str, "table": list[dict], "recommendation": str}
    """
    reasons_a = product_a.get("rank_reasons", {})
    reasons_b = product_b.get("rank_reasons", {})

    rows = []
    for dim in DIM_NAMES:
        score_a = reasons_a.get(dim, 0)
        score_b = reasons_b.get(dim, 0)
        winner = "A" if score_a > score_b else "B" if score_b > score_a else "平"
        rows.append({
            "dimension": DIM_NAMES.get(dim, dim),
            "product_a": f"{score_a:.2f}",
            "product_b": f"{score_b:.2f}",
            "winner": winner,
        })

    name_a = product_a.get("name", "A")
    name_b = product_b.get("name", "B")

    # Determine recommendation
    score_a = product_a.get("rank_score", 0)
    score_b = product_b.get("rank_score", 0)

    if abs(score_a - score_b) < 0.05:
        rec = f"{name_a}和{name_b}都很适合你，看你更看重什么"
    elif score_a > score_b:
        rec = f"综合来看更推荐{name_a}"
    else:
        rec = f"综合来看更推荐{name_b}"

    return {
        "title": f"{name_a} vs {name_b}",
        "table": rows,
        "recommendation": rec,
    }


def explain_counterfactual(
    excluded_product: dict,
    ranked_products: list[dict],
    user_profile: dict | None = None,
) -> str:
    """Explain why a specific product was not recommended.

    Answers: "为什么没推荐 X？"
    """
    excluded_name = excluded_product.get("name", "该商品")
    excluded_reasons = excluded_product.get("rank_reasons", {})

    if not ranked_products:
        return f"{excluded_name}不在候选范围内"

    top = ranked_products[0]
    top_reasons = top.get("rank_reasons", {})

    # Find the weakest dimension of the excluded product
    if excluded_reasons:
        weakest = min(excluded_reasons, key=excluded_reasons.get)
        weakest_name = DIM_NAMES.get(weakest, weakest)

        # Compare with top product's strongest
        strongest = max(top_reasons, key=top_reasons.get) if top_reasons else None
        strongest_name = DIM_NAMES.get(strongest, strongest) if strongest else ""

        if strongest and excluded_reasons.get(weakest, 0) < top_reasons.get(strongest, 0):
            return (
                f"{excluded_name}在{weakest_name}方面得分较低，"
                f"而{top.get('name', '推荐商品')}在{strongest_name}方面更优秀"
            )

    return f"{excluded_name}的综合评分低于当前推荐的{top.get('name', '商品')}"
