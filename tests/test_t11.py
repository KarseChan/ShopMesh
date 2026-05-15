"""T1.1 intent classifier tests."""

import pytest

from src.router.intent_classifier import classify_intent
from src.router import semantic_router


@pytest.mark.asyncio
async def test_build_intent_index():
    """Build intent index and verify count."""
    count = await semantic_router.build_intent_index()
    assert count == 75  # 5 intents x 15 samples


@pytest.mark.asyncio
async def test_semantic_classify_search():
    """Semantic router classifies search intent."""
    intent, confidence = await semantic_router.classify("帮我找一杯奶茶")
    assert intent == "search"
    assert confidence > 0


@pytest.mark.asyncio
async def test_classify_intent_returns_source():
    """classify_intent returns intent, confidence, and source."""
    intent, confidence, source = await classify_intent("帮我找一杯奶茶")
    assert intent in ["search", "compare", "recommend", "detail", "order"]
    assert confidence > 0
    assert source in ["semantic", "llm"]
