"""Merchant Ranker — 时效优先 multi-objective ranking for instant delivery.

Separate from the product ranker (src/agents/ranker.py) on purpose: the product
ranker's product_type_match multiplier and price/reputation dimensions don't map
to merchants, and its multiplicative type penalty would zero every store out.
Same *mechanism* though — profile-selected weights + normalized dimension scores
+ weighted fusion — so the design reads as one family.

Dimensions (all normalized to [0,1], higher = better for the user):
  - eta:          faster delivery ↑   (delivery_minutes, lower is better)
  - distance:     closer ↑            (haversine km, lower is better)
  - delivery_fee: cheaper 配送费 ↑     (lower is better)
  - rating:       higher 评分 ↑

Profiles pick weights. `instant_delivery` (default for 秒送) puts most weight on
eta + distance — "急" queries want the fastest nearby store, not the highest rated.
"""

from src.observability.logger import get_logger

logger = get_logger("merchant_ranker")

RANK_PROFILES = {
    # 时效优先:附近 + 快最重要
    "instant_delivery": {
        "eta": 0.40,
        "distance": 0.30,
        "delivery_fee": 0.10,
        "rating": 0.20,
    },
    # 均衡:评分权重更高(非"急"类 query 可用)
    "balanced": {
        "eta": 0.25,
        "distance": 0.25,
        "delivery_fee": 0.10,
        "rating": 0.40,
    },
}

_DEFAULT_PROFILE = "instant_delivery"


def _normalize_lower_better(value: float, lo: float, hi: float) -> float:
    """Map value→[0,1] where a lower raw value scores higher."""
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, 1.0 - (value - lo) / (hi - lo)))


def _normalize_higher_better(value: float, lo: float, hi: float) -> float:
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _reason_text(reasons: dict) -> str:
    parts = []
    if reasons["eta"] >= 0.7:
        parts.append("送达快")
    if reasons["distance"] >= 0.7:
        parts.append("距离近")
    if reasons["delivery_fee"] >= 0.8:
        parts.append("配送费低")
    if reasons["rating"] >= 0.7:
        parts.append("评分高")
    return "、".join(parts[:3]) or "综合较优"


def rank_merchants(
    merchants: list[dict],
    *,
    profile: str = _DEFAULT_PROFILE,
) -> list[dict]:
    """Rank merchants by weighted fusion of 时效/距离/配送费/评分.

    Each merchant dict must carry `delivery_minutes`, `distance_km`, `delivery_fee`,
    `rating` (distance_km is attached by merchant_search after the haversine check).
    Returns a new sorted list; each entry gains `rank_score`, `rank_reasons`,
    `rank_reason_text`.
    """
    if not merchants:
        return []

    weights = RANK_PROFILES.get(profile, RANK_PROFILES[_DEFAULT_PROFILE])

    etas = [m.get("delivery_minutes", 0) for m in merchants]
    dists = [m.get("distance_km", 0.0) for m in merchants]
    fees = [m.get("delivery_fee", 0.0) for m in merchants]
    ratings = [m.get("rating", 0.0) for m in merchants]
    eta_lo, eta_hi = min(etas), max(etas)
    dist_lo, dist_hi = min(dists), max(dists)
    fee_lo, fee_hi = min(fees), max(fees)
    rat_lo, rat_hi = min(ratings), max(ratings)

    ranked = []
    for m in merchants:
        reasons = {
            "eta": round(_normalize_lower_better(m.get("delivery_minutes", 0), eta_lo, eta_hi), 3),
            "distance": round(_normalize_lower_better(m.get("distance_km", 0.0), dist_lo, dist_hi), 3),
            "delivery_fee": round(_normalize_lower_better(m.get("delivery_fee", 0.0), fee_lo, fee_hi), 3),
            "rating": round(_normalize_higher_better(m.get("rating", 0.0), rat_lo, rat_hi), 3),
        }
        composite = sum(reasons[dim] * weights[dim] for dim in reasons)
        ranked.append({
            **m,
            "rank_score": round(composite, 4),
            "rank_reasons": reasons,
            "rank_reason_text": _reason_text(reasons),
        })

    ranked.sort(key=lambda x: x["rank_score"], reverse=True)
    logger.info("merchants_ranked", count=len(ranked), profile=profile,
                top_score=ranked[0]["rank_score"] if ranked else 0)
    return ranked
