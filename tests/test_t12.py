"""T1.2 entity extractor tests."""

import pytest

from src.agents.entity_extractor import extract_entities


@pytest.mark.asyncio
async def test_extract_basic():
    """Extract entities from a simple query."""
    result = await extract_entities("帮我找一杯20块以内的奶茶")
    assert result["category"] == "奶茶"
    assert result["price_max"] is not None
    assert result["price_max"] <= 20
    assert "ambiguous" in result
    assert "ambiguous_fields" in result


@pytest.mark.asyncio
async def test_extract_with_brand():
    """Extract brand from query."""
    result = await extract_entities("推荐一款雅诗兰黛的护肤品")
    assert result["brand"] is not None


@pytest.mark.asyncio
async def test_extract_ambiguous():
    """Ambiguous entities are marked."""
    result = await extract_entities("苹果多少钱")
    assert isinstance(result["ambiguous"], bool)
    assert isinstance(result["ambiguous_fields"], list)


@pytest.mark.asyncio
async def test_extract_returns_all_fields():
    """Result always contains all required fields."""
    result = await extract_entities("随便看看")
    required = ["category", "price_min", "price_max", "brand",
                "scenario", "quantity", "ambiguous", "ambiguous_fields"]
    for key in required:
        assert key in result
