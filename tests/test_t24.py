"""T2.4 Ranker tests."""

import pytest

from src.agents.ranker import (
    rank, explain_rank, _normalize, _score_price,
    _score_reputation, _score_timeliness, _score_personalization,
    select_rank_profile, RANK_PROFILES,
)


# === Helper ===

def _make_products():
    return [
        {"name": "古茗奶茶", "price": 15, "stock": 999, "platform_id": "tb", "brand": "古茗", "promotion_id": "promo_03"},
        {"name": "蜜雪冰城", "price": 4, "stock": 9999, "platform_id": "tb", "brand": "蜜雪冰城", "promotion_id": None},
        {"name": "喜茶", "price": 28, "stock": 500, "platform_id": "tb", "brand": "喜茶", "promotion_id": None},
    ]


# === Unit Tests ===

def test_normalize():
    assert _normalize(5, 0, 10) == 0.5
    assert _normalize(0, 0, 10) == 0.0
    assert _normalize(10, 0, 10) == 1.0
    assert _normalize(5, 5, 5) == 0.5  # same min/max


def test_score_price():
    products = _make_products()
    # Cheapest product gets highest score
    score_cheap = _score_price(products[1], products)  # 4 yuan
    score_expensive = _score_price(products[2], products)  # 28 yuan
    assert score_cheap > score_expensive


def test_score_reputation():
    product = {"stock": 500}
    score = _score_reputation(product)
    assert 0 < score < 1


def test_score_timeliness():
    assert _score_timeliness({"platform_id": "jd"}) > _score_timeliness({"platform_id": "pdd"})
    assert _score_timeliness({"platform_id": "tb"}) == 0.6


def test_score_personalization_brand_match():
    profile = {"preferred_brands": ["古茗"], "price_sensitivity": 0.5}
    product_match = {"brand": "古茗", "price": 15}
    product_no_match = {"brand": "喜茶", "price": 15}

    score_match = _score_personalization(product_match, profile)
    score_no_match = _score_personalization(product_no_match, profile)
    assert score_match > score_no_match


def test_score_personalization_price_sensitivity():
    # High sensitivity user prefers cheap products
    profile_high = {"price_sensitivity": 0.9, "preferred_brands": []}
    profile_low = {"price_sensitivity": 0.1, "preferred_brands": []}
    cheap = {"brand": "", "price": 10}
    expensive = {"brand": "", "price": 1000}

    assert _score_personalization(cheap, profile_high) > _score_personalization(expensive, profile_high)


def test_select_rank_profile_default():
    assert select_rank_profile({}) == "default"


def test_select_rank_profile_price_sensitive():
    assert select_rank_profile({"preference": "便宜实惠"}) == "price_sensitive"


def test_select_rank_profile_quality_sensitive():
    assert select_rank_profile({"preference": "口碑好"}) == "quality_sensitive"


def test_select_rank_profile_scenario_preference():
    entities = {"soft_requirements": [
        {"text": "夏天穿", "type": "season_scene", "importance": 0.8},
        {"text": "不容易皱", "type": "functional_preference", "importance": 0.9},
    ]}
    assert select_rank_profile(entities) == "scenario_preference"


# === Integration Tests ===

@pytest.mark.asyncio
async def test_rank_basic():
    """Rank products and get sorted results."""
    products = _make_products()
    ranked = await rank(products, search_scores=[0.9, 0.7, 0.8])
    assert len(ranked) == 3
    # Should be sorted by rank_score descending
    assert ranked[0]["rank_score"] >= ranked[1]["rank_score"]
    assert ranked[1]["rank_score"] >= ranked[2]["rank_score"]
    # Each product should have rank_reasons
    for p in ranked:
        assert "rank_score" in p
        assert "rank_reasons" in p
        assert len(p["rank_reasons"]) == 7


@pytest.mark.asyncio
async def test_rank_empty():
    assert await rank([]) == []


@pytest.mark.asyncio
async def test_rank_with_profile():
    """User profile affects ranking."""
    products = _make_products()
    profile = {"preferred_brands": ["蜜雪冰城"], "price_sensitivity": 0.9}
    ranked = await rank(products, search_scores=[0.8, 0.8, 0.8], user_profile=profile)
    # 蜜雪冰城 should rank higher due to brand match + price sensitivity
    assert ranked[0]["brand"] == "蜜雪冰城"


@pytest.mark.asyncio
async def test_rank_with_entities():
    """Entities affect weight adjustment."""
    products = _make_products()
    entities = {"scenario": "送礼"}
    ranked = await rank(products, search_scores=[0.8, 0.8, 0.8], entities=entities)
    # With gift scenario, reputation weight increases
    assert len(ranked) == 3


@pytest.mark.asyncio
async def test_rank_preserves_product_fields():
    """Ranked products retain original fields."""
    products = _make_products()
    ranked = await rank(products)
    for p in ranked:
        assert "name" in p
        assert "price" in p
        assert "platform_id" in p


@pytest.mark.asyncio
async def test_rank_custom_weights():
    """Custom weights override defaults."""
    products = _make_products()
    custom = {"product_type_match": 0, "attribute_match": 0, "semantic_rerank": 0,
              "relevance": 1.0, "price": 0, "reputation": 0, "personalization": 0}
    ranked = await rank(products, search_scores=[0.9, 0.5, 0.7], weights=custom)
    # With pure relevance weighting, order should match search scores
    assert ranked[0]["name"] == "古茗奶茶"  # 0.9


@pytest.mark.asyncio
async def test_explain_rank():
    """explain_rank returns a human-readable reason."""
    products = _make_products()
    ranked = await rank(products, search_scores=[0.9, 0.7, 0.8])
    explanation = explain_rank(ranked[0])
    assert isinstance(explanation, str)
    assert len(explanation) > 0


def test_explain_rank_with_promotion():
    """Promotion mention in explanation."""
    product = {
        "rank_score": 0.85,
        "rank_reasons": {"product_type_match": 1.0, "attribute_match": 0.7,
                         "relevance": 0.9, "price": 0.8, "reputation": 0.5,
                         "personalization": 0.7},
        "promotion_id": "promo_01",
    }
    explanation = explain_rank(product)
    assert "促销" in explanation
