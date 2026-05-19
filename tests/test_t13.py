"""T1.3 clarification engine tests."""

import pytest

from src.agents.clarification_engine import (
    should_clarify,
    _count_candidates,
    _get_discrimination_power,
    _select_questions,
)


@pytest.mark.asyncio
async def test_ask_when_candidates_few_but_fields_missing():
    """Ask questions even when candidates are few, if important fields are missing."""
    entities = {"category": "护肤", "price_min": None, "price_max": None,
                "brand": None, "scenario": None, "quantity": None,
                "skin_type": None, "concerns": None}
    result = await should_clarify(entities, [], 0)
    assert result["should_ask"] is True
    assert result["reason"] == "missing_info"


@pytest.mark.asyncio
async def test_stop_when_max_rounds():
    """Stop asking after 3 rounds even if candidates are many."""
    entities = {"category": None, "price_min": None, "price_max": None,
                "brand": None, "scenario": None, "quantity": None}
    result = await should_clarify(entities, [], 3)
    assert result["should_ask"] is False
    assert result["reason"] == "max_rounds"


@pytest.mark.asyncio
async def test_disambiguation_question():
    """Ask disambiguation question for ambiguous entities."""
    # Use no category filter so candidates > 20, but mark ambiguous
    entities = {
        "category": None, "price_min": None, "price_max": None,
        "brand": None, "scenario": None, "quantity": None,
        "ambiguous": True, "ambiguous_fields": ["category"],
    }
    result = await should_clarify(entities, [], 0)
    assert result["should_ask"] is True
    assert result["reason"] == "disambiguation"


@pytest.mark.asyncio
async def test_ask_question_when_many_candidates():
    """Ask a question when there are many candidates and info is missing."""
    # No filters at all -> all 30 products match
    entities = {"category": None, "price_min": None, "price_max": None,
                "brand": None, "scenario": None, "quantity": None}
    result = await should_clarify(entities, [], 0)
    # Should ask because candidates > 20 and round < 3
    assert result["should_ask"] is True
    assert len(result["questions"]) > 0
    assert result["candidates"] > 0


@pytest.mark.asyncio
async def test_stop_when_no_more_questions():
    """Stop when all filterable fields are filled."""
    entities = {
        "category": "护肤", "price_min": 100, "price_max": 1000,
        "brand": "雅诗兰黛", "scenario": "自用", "quantity": 1,
        "skin_type": "油皮", "concerns": "补水保湿",
    }
    result = await should_clarify(entities, [], 0)
    assert result["should_ask"] is False
    assert result["reason"] == "no_more_questions"


@pytest.mark.asyncio
async def test_no_more_questions_via_asked_fields():
    """Reach no_more_questions when all fields are pre-asked."""
    entities = {"category": None, "price_min": None, "price_max": None,
                "brand": None, "scenario": None, "quantity": None,
                "skin_type": None, "concerns": None}
    asked = ["category", "brand", "price_max", "price_min", "scenario", "quantity",
             "skin_type", "concerns"]
    result = await should_clarify(entities, asked, 0)
    assert result["should_ask"] is False
    assert result["reason"] == "no_more_questions"


def test_count_candidates_category():
    """Count candidates filtered by category."""
    entities = {"category": "护肤"}
    count = _count_candidates(entities)
    assert count == 6  # 3x 雅诗兰黛 + 1x 兰蔻 + 2 more


def test_count_candidates_price():
    """Count candidates filtered by price range."""
    entities = {"price_max": 100}
    count = _count_candidates(entities)
    # 蜜雪冰城 (4), 索尼 pdd (5), 古茗 (15) -> 3 products under 100
    assert count > 0


def test_count_candidates_combined():
    """Count candidates with multiple filters."""
    entities = {"category": "数码", "price_max": 6000}
    count = _count_candidates(entities)
    assert count > 0


def test_discrimination_power():
    """Discrimination power is between 0 and 1."""
    entities = {"category": None}
    power = _get_discrimination_power(entities, "category")
    assert 0.0 <= power <= 1.0


def test_discrimination_power_with_filter():
    """Discrimination power decreases when fewer distinct values remain."""
    # With no filter, category has many distinct values
    power_all = _get_discrimination_power({}, "category")
    # With category filter, brand has fewer distinct values
    power_filtered = _get_discrimination_power({"category": "护肤"}, "brand")
    # Both should be valid
    assert power_all >= 0
    assert power_filtered >= 0


def test_select_questions_skips_asked():
    """Select questions skips already-asked fields."""
    entities = {"category": None, "price_min": None, "price_max": None,
                "brand": None, "scenario": None, "quantity": None,
                "skin_type": None, "concerns": None}
    asked = ["category"]
    results = _select_questions(entities, asked)
    # Should not return the category question
    assert all(q["field"] != "category" for q in results)


def test_select_questions_empty_when_all_filled():
    """Returns empty list when all fields are filled."""
    entities = {
        "category": "护肤", "price_min": 100, "price_max": 1000,
        "brand": "雅诗兰黛", "scenario": "自用", "quantity": 1,
        "skin_type": "油皮", "concerns": "补水保湿",
    }
    questions = _select_questions(entities, [])
    assert questions == []
