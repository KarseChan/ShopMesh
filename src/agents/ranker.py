"""Ranker — multi-objective ranking engine with profile-based weighted fusion.

Architecture:
1. Entity Extractor outputs hard_constraints + soft_requirements
2. Ranker computes 6 dimension scores per product
3. Profile selector picks a stable weight profile based on query characteristics
4. Weighted fusion + product_type_match penalty → final rank

Dimensions:
- product_type_match: structured type field match (multiplicative penalty)
- attribute_match: soft_requirements keyword/synonym match
- relevance: vector similarity score (from Qdrant search)
- price: price competitiveness (lower = better)
- reputation: product popularity
- personalization: user preference match
"""

import re
from pathlib import Path

import yaml

from src.config import config
from src.observability.logger import get_logger

logger = get_logger("ranker")

# --- Synonym loading ---

_SYNONYMS: dict[str, list[str]] | None = None


def _load_synonyms() -> dict[str, list[str]]:
    """Load synonym table from configs/ranking/synonyms.yaml."""
    global _SYNONYMS
    if _SYNONYMS is not None:
        return _SYNONYMS

    synonyms_path = Path(__file__).resolve().parent.parent.parent / "configs" / "ranking" / "synonyms.yaml"
    try:
        with open(synonyms_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _SYNONYMS = data.get("synonyms", {})
    except Exception:
        _SYNONYMS = {}
    return _SYNONYMS


# --- Rank Profiles ---

RANK_PROFILES = {
    "default": {
        "product_type_match": 0.25,
        "attribute_match": 0.20,
        "relevance": 0.20,
        "price": 0.15,
        "reputation": 0.10,
        "personalization": 0.10,
    },
    "price_sensitive": {
        "product_type_match": 0.20,
        "attribute_match": 0.15,
        "price": 0.30,
        "relevance": 0.15,
        "reputation": 0.15,
        "personalization": 0.05,
    },
    "quality_sensitive": {
        "product_type_match": 0.20,
        "attribute_match": 0.20,
        "reputation": 0.25,
        "relevance": 0.15,
        "price": 0.10,
        "personalization": 0.10,
    },
    "scenario_preference": {
        "product_type_match": 0.20,
        "attribute_match": 0.30,
        "relevance": 0.15,
        "price": 0.10,
        "reputation": 0.15,
        "personalization": 0.10,
    },
}

# Platform delivery speed scores (higher = faster)
PLATFORM_SPEED = {
    "jd": 0.9,
    "tb": 0.6,
    "pdd": 0.5,
}


# --- Profile Selector ---

def select_rank_profile(entities: dict) -> str:
    """Select a rank profile based on query characteristics.

    Returns profile name key into RANK_PROFILES.
    """
    soft_reqs = entities.get("soft_requirements", [])
    preference = entities.get("preference", "")

    if preference and any(kw in preference for kw in ["便宜", "性价比", "实惠", "省钱"]):
        return "price_sensitive"

    if preference and any(kw in preference for kw in ["口碑", "销量", "大牌", "知名", "品牌", "质量"]):
        return "quality_sensitive"

    if len(soft_reqs) >= 2:
        return "scenario_preference"

    return "default"


# --- Utility ---

def _normalize(value: float, min_val: float, max_val: float) -> float:
    if max_val == min_val:
        return 0.5
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


# --- Attribute Match (soft_requirements) ---

def _build_product_text(product: dict) -> str:
    """Build searchable text from product fields."""
    parts = [
        product.get("name", ""),
        product.get("product_type", ""),
    ]
    parts.extend(product.get("features", []))
    return " ".join(parts)


def _keyword_match_score(req_text: str, product_text: str) -> float:
    """Score how well a requirement matches product text.

    1. Split req_text into keywords
    2. For each keyword, check direct match + synonym expansion
    3. Return hit rate (hits / total keywords)
    """
    if not req_text or not product_text:
        return 0.0

    product_lower = product_text.lower()

    # Split requirement into keywords
    keywords = re.split(r"[，,、\s]+", req_text.strip())
    keywords = [kw for kw in keywords if kw]

    if not keywords:
        return 0.0

    synonyms = _load_synonyms()
    hits = 0

    for kw in keywords:
        kw_lower = kw.lower()
        # Direct match
        if kw_lower in product_lower:
            hits += 1
            continue
        # Synonym expansion: check if any synonym of kw appears in product
        syns = synonyms.get(kw, [])
        if any(s.lower() in product_lower for s in syns):
            hits += 1
            continue
        # Reverse check: if kw is a synonym of some key, check the key
        for key, syn_list in synonyms.items():
            if kw_lower == key.lower() or kw_lower in [s.lower() for s in syn_list]:
                # kw is in synonym table; check if key or any sibling synonym is in product
                if key.lower() in product_lower:
                    hits += 1
                    break
                if any(s.lower() in product_lower for s in syn_list):
                    hits += 1
                    break

    return hits / len(keywords)


def _score_attribute_match(product: dict, soft_requirements: list[dict]) -> float:
    """Score product against all soft_requirements using keyword+synonym matching.

    Returns weighted average of per-requirement match scores.
    """
    if not soft_requirements:
        return 0.5

    product_text = _build_product_text(product)
    total_score = 0.0
    total_weight = 0.0

    for req in soft_requirements:
        req_text = req.get("text", "")
        importance = req.get("importance", 0.7)
        score = _keyword_match_score(req_text, product_text)
        total_score += score * importance
        total_weight += importance

    return total_score / total_weight if total_weight > 0 else 0.5


# --- Dimension Scoring Functions ---

def _score_relevance(product: dict, search_score: float) -> float:
    return max(0.0, min(1.0, search_score))


def _score_price(product: dict, all_products: list[dict]) -> float:
    prices = [p["price"] for p in all_products if p["price"] > 0]
    if not prices:
        return 0.5
    return 1.0 - _normalize(product["price"], min(prices), max(prices))


def _score_reputation(product: dict) -> float:
    reputation = product.get("reputation")
    if reputation is not None:
        return max(0.0, min(1.0, float(reputation)))
    stock = product.get("stock", 0)
    return _normalize(stock, 0, 1000)


def _score_timeliness(product: dict) -> float:
    platform = product.get("platform_id", "")
    return PLATFORM_SPEED.get(platform, 0.5)


def _score_personalization(product: dict, user_profile: dict) -> float:
    score = 0.5
    preferred_brands = user_profile.get("preferred_brands", [])
    if preferred_brands and product.get("brand") in preferred_brands:
        score += 0.3
    sensitivity = user_profile.get("price_sensitivity", 0.5)
    price = product.get("price", 0)
    if sensitivity > 0.7 and price < 100:
        score += 0.1
    elif sensitivity < 0.3 and price > 500:
        score += 0.1
    return min(1.0, score)


def _score_product_type_match(product: dict, product_type: str | None) -> float:
    """Product type match: penalty multiplier (not in weighted sum)."""
    if not product_type:
        return 1.0
    if product.get("product_type") == product_type:
        return 1.0
    if product_type in product.get("name", ""):
        return 0.9
    if product_type in " ".join(product.get("features", [])):
        return 0.8
    return 0.1


# --- Main Rank Function ---

def rank(
    products: list[dict],
    search_scores: list[float] | None = None,
    user_profile: dict | None = None,
    entities: dict | None = None,
    weights: dict | None = None,
) -> list[dict]:
    """Rank products using profile-based multi-objective weighted fusion.

    Args:
        products: List of product dicts
        search_scores: Vector similarity scores (same order as products)
        user_profile: User preference profile
        entities: Current query entities (contains soft_requirements)
        weights: Custom weights override (bypasses profile selection)

    Returns:
        List of products sorted by composite score, each with
        "rank_score" and "rank_reasons".
    """
    if not products:
        return []

    profile = user_profile or {}
    ents = entities or {}
    scores = search_scores or [0.5] * len(products)

    soft_requirements = ents.get("soft_requirements", [])
    product_type = ents.get("product_type")

    # Select weight profile
    if weights:
        final_weights = weights
        profile_name = "custom"
    else:
        profile_name = select_rank_profile(ents)
        final_weights = RANK_PROFILES[profile_name]

    ranked = []
    for i, product in enumerate(products):
        search_score = scores[i] if i < len(scores) else 0.5

        # 6 dimension scores
        reasons = {
            "product_type_match": round(_score_product_type_match(product, product_type), 3),
            "attribute_match": round(_score_attribute_match(product, soft_requirements), 3),
            "relevance": round(_score_relevance(product, search_score), 3),
            "price": round(_score_price(product, products), 3),
            "reputation": round(_score_reputation(product), 3),
            "personalization": round(_score_personalization(product, profile), 3),
        }

        # Weighted fusion
        composite = sum(
            reasons[dim] * final_weights[dim]
            for dim in reasons
        )

        # Product type match penalty (multiplicative)
        pt_match = reasons["product_type_match"]
        composite *= pt_match

        ranked.append({
            **product,
            "rank_score": round(composite, 4),
            "rank_reasons": reasons,
        })

    ranked.sort(key=lambda x: x["rank_score"], reverse=True)

    # Logging
    for i, p in enumerate(ranked):
        logger.info("rank_detail",
                    rank=i + 1,
                    product_id=p.get("product_id", ""),
                    name=p.get("name", ""),
                    platform=p.get("platform_id", ""),
                    rank_score=p["rank_score"],
                    dimensions=p["rank_reasons"])

    logger.info("ranked", count=len(ranked),
                profile=profile_name,
                top_score=ranked[0]["rank_score"] if ranked else 0,
                weights={k: round(v, 2) for k, v in final_weights.items()},
                soft_requirements_count=len(soft_requirements))

    return ranked


def explain_rank(product: dict) -> str:
    """Generate a brief explanation for why a product was ranked at its position."""
    reasons = product.get("rank_reasons", {})
    score = product.get("rank_score", 0)

    parts = []
    if reasons:
        # Exclude product_type_match from "best dimension" (it's a penalty, not a strength)
        scorable = {k: v for k, v in reasons.items() if k != "product_type_match"}
        if scorable:
            best_dim = max(scorable, key=scorable.get)
            dim_names = {
                "attribute_match": "属性匹配度高",
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
