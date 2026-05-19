"""Ranker — multi-objective ranking engine with weighted fusion.

Dimensions:
- relevance: vector similarity score (from Qdrant search)
- price: price competitiveness (lower = better, adjusted by user sensitivity)
- reputation: product popularity (stock as proxy)
- timeliness: delivery speed (platform-based)
- personalization: user preference match (brand, price range)

Each product gets a composite score + per-dimension explanation.
"""

from src.config import config
from src.observability.logger import get_logger

logger = get_logger("ranker")

# Default dimension weights (sum = 1.0)
DEFAULT_WEIGHTS = {
    "relevance": 0.35,
    "price": 0.25,
    "reputation": 0.15,
    "timeliness": 0.10,
    "personalization": 0.15,
}

# Platform delivery speed scores (higher = faster)
PLATFORM_SPEED = {
    "jd": 0.9,   # 京东: 自营次日达
    "tb": 0.6,   # 淘宝: 商家发货 2-3 天
    "pdd": 0.5,  # 拼多多: 3-5 天
}


def _normalize(value: float, min_val: float, max_val: float) -> float:
    """Normalize a value to 0-1 range."""
    if max_val == min_val:
        return 0.5
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


def _score_relevance(product: dict, search_score: float) -> float:
    """Relevance score from vector search similarity."""
    return max(0.0, min(1.0, search_score))


def _score_price(product: dict, all_products: list[dict]) -> float:
    """Price score: cheaper products score higher within the candidate set."""
    prices = [p["price"] for p in all_products if p["price"] > 0]
    if not prices:
        return 0.5
    # Invert: lower price = higher score
    return 1.0 - _normalize(product["price"], min(prices), max(prices))


def _score_reputation(product: dict) -> float:
    """Reputation score: use explicit reputation field, fallback to stock proxy."""
    reputation = product.get("reputation")
    if reputation is not None:
        return max(0.0, min(1.0, float(reputation)))
    # Fallback: stock as proxy for popularity
    stock = product.get("stock", 0)
    return _normalize(stock, 0, 1000)


def _score_timeliness(product: dict) -> float:
    """Timeliness score: platform delivery speed."""
    platform = product.get("platform_id", "")
    return PLATFORM_SPEED.get(platform, 0.5)


def _score_personalization(product: dict, user_profile: dict) -> float:
    """Personalization score: match with user preferences."""
    score = 0.5  # baseline

    # Brand preference match
    preferred_brands = user_profile.get("preferred_brands", [])
    if preferred_brands and product.get("brand") in preferred_brands:
        score += 0.3

    # Price sensitivity match
    sensitivity = user_profile.get("price_sensitivity", 0.5)
    price = product.get("price", 0)
    # High sensitivity users prefer lower prices
    if sensitivity > 0.7 and price < 100:
        score += 0.1
    elif sensitivity < 0.3 and price > 500:
        score += 0.1  # Low sensitivity = willing to pay more

    return min(1.0, score)


def _score_product_type_match(product: dict, product_type: str | None) -> float:
    """Product type match score: penalize products that don't match the requested type.

    Priority:
    1. Exact match on product_type field (structured data)
    2. Keyword match in product name
    3. Keyword match in features
    4. No match → strong penalty (0.1x)

    Does NOT check category (categories are broad, e.g., "男装/上装" contains
    T-shirts, shirts, polo shirts, etc.).
    """
    if not product_type:
        return 1.0  # No constraint → no penalty

    # Priority 1: structured field exact match
    if product.get("product_type") == product_type:
        return 1.0

    # Priority 2: keyword in name
    if product_type in product.get("name", ""):
        return 0.9

    # Priority 3: keyword in features
    if product_type in " ".join(product.get("features", [])):
        return 0.8

    # No match → strong penalty
    return 0.1


def _adjust_weights(
    base_weights: dict, user_profile: dict, entities: dict
) -> dict:
    """Adjust weights based on user profile and current context.

    E.g., price-sensitive users get higher price weight.
    """
    weights = dict(base_weights)
    sensitivity = user_profile.get("price_sensitivity", 0.5)

    # Price-sensitive users: increase price weight
    if sensitivity > 0.7:
        shift = 0.1
        weights["price"] += shift
        weights["relevance"] -= shift * 0.5
        weights["timeliness"] -= shift * 0.5

    # Scenario: gift-giving → increase reputation, decrease price
    scenario = entities.get("scenario")
    if scenario and ("送礼" in scenario or "礼物" in scenario):
        weights["reputation"] += 0.1
        weights["price"] -= 0.1

    # Preference: reputation-focused ("口碑好", "销量高", "大牌", "知名")
    preference = entities.get("preference", "")
    if preference and any(kw in preference for kw in ["口碑", "销量", "大牌", "知名", "品牌"]):
        shift = 0.15
        weights["reputation"] += shift
        weights["price"] -= shift * 0.5
        weights["timeliness"] -= shift * 0.5

    # Normalize to sum = 1.0
    total = sum(weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}

    return weights


def rank(
    products: list[dict],
    search_scores: list[float] | None = None,
    user_profile: dict | None = None,
    entities: dict | None = None,
    weights: dict | None = None,
) -> list[dict]:
    """Rank products using multi-objective weighted fusion.

    Args:
        products: List of product dicts
        search_scores: Vector similarity scores (same order as products)
        user_profile: User preference profile
        entities: Current query entities
        weights: Custom weights override

    Returns:
        List of products sorted by composite score, each with:
        - "rank_score": float (0-1)
        - "rank_reasons": dict (per-dimension scores)
    """
    if not products:
        return []

    profile = user_profile or {}
    ents = entities or {}
    scores = search_scores or [0.5] * len(products)

    # Determine weights
    base = weights or DEFAULT_WEIGHTS
    final_weights = _adjust_weights(base, profile, ents)

    ranked = []
    for i, product in enumerate(products):
        search_score = scores[i] if i < len(scores) else 0.5

        # Calculate per-dimension scores
        reasons = {
            "relevance": round(_score_relevance(product, search_score), 3),
            "price": round(_score_price(product, products), 3),
            "reputation": round(_score_reputation(product), 3),
            "timeliness": round(_score_timeliness(product), 3),
            "personalization": round(_score_personalization(product, profile), 3),
        }

        # Weighted fusion
        composite = sum(
            reasons[dim] * final_weights[dim]
            for dim in reasons
        )

        # Product type match penalty: non-matching products get 0.4x score
        product_type = ents.get("product_type")
        pt_match = _score_product_type_match(product, product_type)
        composite *= pt_match

        reasons["product_type_match"] = round(pt_match, 3)

        ranked.append({
            **product,
            "rank_score": round(composite, 4),
            "rank_reasons": reasons,
        })

    # Sort by composite score descending
    ranked.sort(key=lambda x: x["rank_score"], reverse=True)

    # Log per-product ranking details
    for i, p in enumerate(ranked):
        logger.info("rank_detail",
                    rank=i + 1,
                    product_id=p.get("product_id", ""),
                    name=p.get("name", ""),
                    platform=p.get("platform_id", ""),
                    rank_score=p["rank_score"],
                    dimensions=p["rank_reasons"])

    logger.info("ranked", count=len(ranked),
                top_score=ranked[0]["rank_score"] if ranked else 0,
                weights={k: round(v, 2) for k, v in final_weights.items()})

    return ranked


def explain_rank(product: dict) -> str:
    """Generate a brief explanation for why a product was ranked at its position.

    Returns a human-readable string.
    """
    reasons = product.get("rank_reasons", {})
    score = product.get("rank_score", 0)

    parts = []
    # Find the strongest dimension
    if reasons:
        best_dim = max(reasons, key=reasons.get)
        dim_names = {
            "relevance": "与你的需求高度匹配",
            "price": "价格有优势",
            "reputation": "口碑好、销量高",
            "timeliness": "配送速度快",
            "personalization": "符合你的偏好",
        }
        parts.append(dim_names.get(best_dim, best_dim))

    if product.get("promotion_id"):
        parts.append("有促销活动")

    return "，".join(parts) if parts else f"综合评分 {score:.2f}"
