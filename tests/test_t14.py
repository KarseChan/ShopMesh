"""T1.4 disambiguator tests."""

import pytest

from src.agents.disambiguator import (
    disambiguate,
    _build_candidate_map,
    _build_question,
    _get_catalog_candidates,
    _resolve_from_context,
)


@pytest.mark.asyncio
async def test_non_ambiguous_passthrough():
    """Non-ambiguous entities pass through unchanged."""
    entities = {
        "category": "护肤", "brand": None, "price_min": None,
        "price_max": None, "scenario": None, "quantity": None,
        "ambiguous": False, "ambiguous_fields": [],
    }
    result = await disambiguate(entities)
    assert result["resolved"] is True
    assert result["question"] is None
    assert result["entities"]["category"] == "护肤"


@pytest.mark.asyncio
async def test_no_ambiguous_fields():
    """Ambiguous=True but no fields -> resolved."""
    entities = {
        "category": None, "brand": None, "price_min": None,
        "price_max": None, "scenario": None, "quantity": None,
        "ambiguous": True, "ambiguous_fields": [],
    }
    result = await disambiguate(entities)
    assert result["resolved"] is True


def test_build_candidate_map():
    """Candidate map contains terms spanning multiple categories."""
    cmap = _build_candidate_map()
    assert isinstance(cmap, dict)
    # Each entry should have multiple categories
    for term, info in cmap.items():
        assert len(info["categories"]) > 1


def test_get_catalog_candidates_brand():
    """Find catalog candidates for a brand term."""
    candidates = _get_catalog_candidates("brand", "Apple")
    assert len(candidates) > 0
    # Apple should map to 数码
    cats = {c["category"] for c in candidates}
    assert "数码" in cats


def test_get_catalog_candidates_unknown():
    """Unknown term returns empty candidates."""
    candidates = _get_catalog_candidates("brand", "不存在的品牌xyz")
    assert len(candidates) == 0


def test_resolve_from_context():
    """Context can narrow down candidates when category is mentioned."""
    candidates = [
        {"value": "Apple", "category": "数码", "count": 3},
        {"value": "Apple", "category": "食品", "count": 0},
    ]
    context = [{"role": "user", "content": "我想买个数码产品"}]
    resolved = _resolve_from_context("brand", "Apple", candidates, context)
    assert resolved == "Apple"


def test_resolve_from_context_no_hint():
    """Returns None when context has no relevant hints."""
    candidates = [
        {"value": "Apple", "category": "数码", "count": 3},
        {"value": "Apple", "category": "食品", "count": 0},
    ]
    context = [{"role": "user", "content": "今天天气不错"}]
    resolved = _resolve_from_context("brand", "Apple", candidates, context)
    assert resolved is None


def test_build_question_brand():
    """Build disambiguation question for brand."""
    candidates = [
        {"value": "Apple", "category": "数码", "count": 3},
        {"value": "Apple", "category": "食品", "count": 1},
    ]
    q = _build_question("brand", "Apple", candidates)
    assert "Apple" in q
    assert "数码" in q or "食品" in q


def test_build_question_category():
    """Build disambiguation question for category."""
    candidates = [
        {"value": "数码", "category": "数码", "count": 5},
        {"value": "食品", "category": "食品", "count": 2},
    ]
    q = _build_question("category", "电子", candidates)
    assert "电子" in q


@pytest.mark.asyncio
async def test_disambiguate_resolved_single_candidate():
    """Auto-resolve when only one candidate exists."""
    entities = {
        "category": None, "brand": "蜜雪冰城", "price_min": None,
        "price_max": None, "scenario": None, "quantity": None,
        "ambiguous": True, "ambiguous_fields": ["brand"],
    }
    result = await disambiguate(entities)
    # 蜜雪冰城 only appears in 奶茶, so should auto-resolve
    assert result["resolved"] is True
    assert result["entities"]["brand"] == "蜜雪冰城"
