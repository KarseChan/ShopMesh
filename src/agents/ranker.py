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

    # Filter out skipped/redundant types before counting
    _skip_types = _META_REQ_TYPES | _REDUNDANT_REQ_TYPES
    actionable_reqs = [r for r in soft_reqs if r.get("type", "") not in _skip_types]
    if len(actionable_reqs) >= 2:
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


def _keyword_match_score(req_text: str, product_text: str) -> tuple[float, list[str]]:
    """Score how well a requirement matches product text.

    Returns (score, matched_terms) where matched_terms lists what was hit.
    """
    if not req_text or not product_text:
        return 0.0, []

    product_lower = product_text.lower()

    # Split requirement into keywords
    keywords = re.split(r"[，,、\s]+", req_text.strip())
    keywords = [kw for kw in keywords if kw]

    if not keywords:
        return 0.0, []

    synonyms = _load_synonyms()
    hits = 0
    matched_terms = []

    for kw in keywords:
        kw_lower = kw.lower()
        # 1. Direct match
        if kw_lower in product_lower:
            hits += 1
            matched_terms.append(kw)
            continue
        # 2. Exact synonym lookup
        syns = synonyms.get(kw, [])
        matched_syn = _find_in_product(syns, product_lower)
        if matched_syn:
            hits += 1
            matched_terms.append(matched_syn)
            continue
        # 3. Partial match: kw contains a synonym key (e.g. "夏天穿" contains "夏天")
        partial_matched = False
        for key, syn_list in synonyms.items():
            if key in kw_lower:
                # kw contains a synonym key — check if key or its synonyms appear in product
                matched_syn = _find_in_product([key] + syn_list, product_lower)
                if matched_syn:
                    hits += 1
                    matched_terms.append(matched_syn)
                    partial_matched = True
                    break
        if partial_matched:
            continue
        # 4. Reverse: kw is itself a synonym value
        for key, syn_list in synonyms.items():
            if kw_lower in [s.lower() for s in syn_list]:
                matched_syn = _find_in_product([key] + syn_list, product_lower)
                if matched_syn:
                    hits += 1
                    matched_terms.append(matched_syn)
                    break

    return hits / len(keywords), matched_terms


def _find_in_product(terms: list[str], product_lower: str) -> str | None:
    """Find the first term from a list that appears in product text."""
    for t in terms:
        if t.lower() in product_lower:
            return t
    return None


def _terms_match_score(terms: list[str], product_text: str) -> tuple[float, list[str]]:
    """Score how well a list of terms matches product text.

    Used with normalized soft_requirements that have pre-expanded terms.
    Returns (score, matched_terms) where score is hit_ratio and matched_terms lists what was hit.
    """
    if not terms or not product_text:
        return 0.0, []

    product_lower = product_text.lower()
    hits = 0
    matched_terms = []

    for term in terms:
        if term.lower() in product_lower:
            hits += 1
            matched_terms.append(term)

    return hits / len(terms), matched_terms


# Meta-requirement types that are contextual, not product-level attributes.
# These should not be keyword-matched against product text.
_META_REQ_TYPES = {"gift_context", "recipient_context"}

# Soft requirement types that are already covered by hard constraints
# and should not participate in attribute matching.
_REDUNDANT_REQ_TYPES = {"price_preference"}


def _score_attribute_match(
    product: dict, soft_requirements: list[dict]
) -> tuple[float, dict[str, dict]]:
    """Score product against all soft_requirements using keyword+synonym matching.

    Supports both normalized format (with terms list) and legacy format (text only).
    Meta-requirements (gift_context etc.) are skipped — they're contextual, not product attributes.

    Returns (weighted_score, attribute_scores) where attribute_scores maps
    req_text → {"score": float, "matched_terms": list[str]}.
    """
    if not soft_requirements:
        return 0.5, {}

    product_text = _build_product_text(product)
    total_score = 0.0
    total_weight = 0.0
    attribute_scores = {}

    for req in soft_requirements:
        req_type = req.get("type", "")
        importance = req.get("importance", 0.7)
        display_key = req.get("canonical") or req.get("raw_text", "") or req.get("text", "")

        # Skip meta-requirements (gift_context etc.) — they don't match product attributes
        if req_type in _META_REQ_TYPES:
            attribute_scores[display_key] = {
                "score": 0.0,
                "matched_terms": [],
                "skipped": True,
            }
            continue

        # Skip redundant requirements (price_preference etc.) — already covered by hard constraints
        if req_type in _REDUNDANT_REQ_TYPES:
            attribute_scores[display_key] = {
                "score": 0.0,
                "matched_terms": [],
                "skipped": True,
            }
            continue

        # Normalized format: use pre-expanded terms
        if "terms" in req and req["terms"]:
            score, matched = _terms_match_score(req["terms"], product_text)
        else:
            # Legacy format: use text with keyword matching
            score, matched = _keyword_match_score(req.get("text", ""), product_text)

        total_score += score * importance
        total_weight += importance
        attribute_scores[display_key] = {
            "score": round(score, 3),
            "matched_terms": matched,
        }

    final_score = total_score / total_weight if total_weight > 0 else 0.5
    return final_score, attribute_scores


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


def _score_memory_boost(product: dict, memory_signals: dict | None) -> float:
    """Score boost/penalty from Qdrant memory signals.

    Returns a delta in [-0.20, +0.20]:
    - positive_interest: +0.10~0.15 if product brand/type matches
    - negative_feedback: -0.10~0.15 if product brand/type matches
    - stable_preference: 0.0 (handled via attribute_match)
    - recent_task_memory: +0.05~0.08 if product brand/type matches
    """
    if not memory_signals:
        return 0.0

    product_brand = product.get("brand", "")
    product_type = product.get("product_type", "")

    pos_boost = 0.0
    neg_penalty = 0.0
    recent_boost = 0.0

    # Negative feedback (highest priority — overrides positive)
    for mem in memory_signals.get("negative_feedback", []):
        mem_entities = mem.get("entities", {})
        if product_brand and product_brand == mem_entities.get("brand"):
            neg_penalty = min(neg_penalty, -0.15)
        elif product_type and product_type == mem_entities.get("product_type"):
            neg_penalty = min(neg_penalty, -0.10)

    # Positive interest
    for mem in memory_signals.get("positive_interest", []):
        mem_entities = mem.get("entities", {})
        if product_brand and product_brand == mem_entities.get("brand"):
            pos_boost = max(pos_boost, 0.15)
        elif product_type and product_type == mem_entities.get("product_type"):
            pos_boost = max(pos_boost, 0.10)

    # Recent task memory (mild boost)
    for mem in memory_signals.get("recent_task_memory", []):
        mem_entities = mem.get("entities", {})
        if product_brand and product_brand == mem_entities.get("brand"):
            recent_boost = max(recent_boost, 0.08)
        elif product_type and product_type == mem_entities.get("product_type"):
            recent_boost = max(recent_boost, 0.05)

    # If both positive and negative, negative wins (防矛盾)
    if neg_penalty < 0:
        total = neg_penalty
    else:
        total = pos_boost + recent_boost

    return max(-0.20, min(0.20, total))


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
    # Fuzzy: check if product_type shares a root with the product's type
    # e.g., "鞋子" shares "鞋" with "运动鞋", "皮鞋"
    p_type = product.get("product_type", "")
    if p_type and len(product_type) >= 2:
        # Check if the core noun (last char) appears in the product's type
        core = product_type[-1]  # "鞋子" → "鞋"
        if core in p_type:
            return 0.7
    return 0.1


# --- Main Rank Function ---

def rank(
    products: list[dict],
    search_scores: list[float] | None = None,
    user_profile: dict | None = None,
    entities: dict | None = None,
    weights: dict | None = None,
    memory_signals: dict | None = None,
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

    # Detect meta-requirements (gift_context etc.) for scenario boost
    has_meta_scenario = any(
        req.get("type", "") in _META_REQ_TYPES
        for req in soft_requirements
    )

    ranked = []
    for i, product in enumerate(products):
        search_score = scores[i] if i < len(scores) else 0.5

        # Attribute match (returns score + per-requirement breakdown)
        attr_score, attr_scores = _score_attribute_match(product, soft_requirements)

        # 6 dimension scores
        reasons = {
            "product_type_match": round(_score_product_type_match(product, product_type), 3),
            "attribute_match": round(attr_score, 3),
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

        # Scenario boost: for meta-scenarios (gift etc.), boost by reputation + price fit
        # since attribute_match can't capture contextual requirements
        if has_meta_scenario:
            scenario_boost = reasons["reputation"] * 0.06 + reasons["price"] * 0.04
            composite += scenario_boost

        # Memory signal boost: positive/negative feedback from Qdrant memories
        if memory_signals:
            mem_boost = _score_memory_boost(product, memory_signals)
            composite += mem_boost
            reasons["memory_boost"] = round(mem_boost, 3)

        entry = {
            **product,
            "rank_score": round(composite, 4),
            "rank_reasons": reasons,
        }
        if attr_scores:
            entry["attribute_scores"] = attr_scores
        ranked.append(entry)

    ranked.sort(key=lambda x: x["rank_score"], reverse=True)

    # Logging
    for i, p in enumerate(ranked):
        log_kwargs = {
            "rank": i + 1,
            "product_id": p.get("product_id", ""),
            "name": p.get("name", ""),
            "platform": p.get("platform_id", ""),
            "rank_score": p["rank_score"],
            "dimensions": p["rank_reasons"],
        }
        if "attribute_scores" in p:
            log_kwargs["attribute_scores"] = p["attribute_scores"]
        logger.info("rank_detail", **log_kwargs)

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
