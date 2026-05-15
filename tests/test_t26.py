"""T2.6 Explainer tests."""

import pytest
from unittest.mock import AsyncMock, patch

from src.agents.explainer import (
    generate_reason, polish_reason, generate_comparison,
    explain_counterfactual, _top_reasons, _price_text,
)


# === Helpers ===

def _make_ranked(name="古茗奶茶", price=15, score=0.85, promo=None):
    return {
        "name": name,
        "brand": "古茗",
        "price": price,
        "rank_score": score,
        "rank_reasons": {
            "relevance": 0.9,
            "price": 0.8,
            "reputation": 0.7,
            "timeliness": 0.6,
            "personalization": 0.75,
        },
        "features": ["芋泥", "现熬"],
        "promo_desc": promo,
        "suggest_message": None,
    }


# === Unit Tests ===

def test_top_reasons():
    reasons = {"relevance": 0.9, "price": 0.8, "reputation": 0.5}
    top = _top_reasons(reasons, n=2)
    assert len(top) == 2
    assert "与你需求匹配" in top[0]


def test_price_text_simple():
    assert _price_text({"price": 15}) == "¥15"


def test_price_text_with_promo():
    assert _price_text({"price": 15, "promo_desc": "满100减20"}) == "¥15（满100减20）"


# === Generate Reason ===

def test_generate_reason_basic():
    product = _make_ranked()
    reason = generate_reason(product)
    assert "古茗奶茶" not in reason or "¥" in reason
    assert "¥15" in reason


def test_generate_reason_with_brand_preference():
    product = _make_ranked()
    profile = {"preferred_brands": ["古茗"]}
    reason = generate_reason(product, user_profile=profile)
    assert "古茗" in reason


def test_generate_reason_with_promotion():
    product = _make_ranked(promo="满100减20")
    product["suggest_message"] = "再买2件可享7折"
    reason = generate_reason(product)
    assert "¥" in reason


# === Polish Reason (mocked LLM) ===

@pytest.mark.asyncio
async def test_polish_reason_success():
    product = _make_ranked()
    structured = "这款与你需求匹配，¥15"

    with patch("src.agents.explainer.get_llm") as mock_get_llm:
        mock_llm = AsyncMock()
        mock_llm.chat_json = AsyncMock(return_value={"reason": "这款奶茶很适合你，才15元"})
        mock_get_llm.return_value = mock_llm

        result = await polish_reason(product, structured)
        assert "适合" in result or "15" in result


@pytest.mark.asyncio
async def test_polish_reason_fallback():
    """LLM failure falls back to structured reason."""
    product = _make_ranked()
    structured = "这款与你需求匹配，¥15"

    with patch("src.agents.explainer.get_llm") as mock_get_llm:
        mock_llm = AsyncMock()
        mock_llm.chat_json = AsyncMock(side_effect=Exception("LLM down"))
        mock_get_llm.return_value = mock_llm

        result = await polish_reason(product, structured)
        assert result == structured


# === Comparison ===

def test_generate_comparison_close_scores():
    """Close scores get a neutral recommendation."""
    a = _make_ranked("古茗奶茶", 15, 0.85)
    b = _make_ranked("喜茶", 28, 0.83)
    comp = generate_comparison(a, b)

    assert comp["title"] == "古茗奶茶 vs 喜茶"
    assert len(comp["table"]) == 5
    assert "都很适合" in comp["recommendation"] or "更推荐" in comp["recommendation"]


def test_generate_comparison_clear_winner():
    """Clear winner gets a definitive recommendation."""
    a = _make_ranked("古茗奶茶", 15, 0.90)
    b = _make_ranked("喜茶", 28, 0.60)
    comp = generate_comparison(a, b)

    assert "更推荐" in comp["recommendation"]
    assert "古茗" in comp["recommendation"]


def test_generate_comparison_table_structure():
    a = _make_ranked("A", 10, 0.8)
    b = _make_ranked("B", 20, 0.7)
    comp = generate_comparison(a, b)

    for row in comp["table"]:
        assert "dimension" in row
        assert "product_a" in row
        assert "product_b" in row
        assert "winner" in row


# === Counterfactual ===

def test_explain_counterfactual():
    excluded = _make_ranked("喜茶", 28, 0.60)
    excluded["rank_reasons"]["reputation"] = 0.3  # weakest

    top = _make_ranked("古茗奶茶", 15, 0.85)
    explanation = explain_counterfactual(excluded, [top])

    assert "喜茶" in explanation
    assert len(explanation) > 0


def test_explain_counterfactual_empty_ranked():
    excluded = _make_ranked("喜茶", 28, 0.60)
    explanation = explain_counterfactual(excluded, [])
    assert "不在候选范围内" in explanation
