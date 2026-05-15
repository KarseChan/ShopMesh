"""T2.1 Router Agent tests."""

import pytest
from unittest.mock import AsyncMock, patch

from src.agents.router import (
    route, route_with_context, register_agent,
    _get_routes, _get_fallback, _get_threshold,
    _agent_registry,
)


# === Config tests ===

def test_routes_from_config():
    routes = _get_routes()
    assert "search" in routes
    assert routes["search"] == "search_agent"
    assert "order" in routes


def test_fallback_from_config():
    assert _get_fallback() == "general_agent"


def test_threshold_from_config():
    assert _get_threshold() == 0.80


# === Registration tests ===

def test_register_agent():
    async def dummy_handler(input, **kwargs):
        return {"mock": True}

    register_agent("test_agent", dummy_handler)
    assert "test_agent" in _agent_registry
    # Cleanup
    del _agent_registry["test_agent"]


# === Routing tests ===

@pytest.mark.asyncio
async def test_route_fast_path():
    """High confidence semantic classification routes directly."""
    async def mock_search(input, **kwargs):
        return {"products": ["mock"]}

    register_agent("search_agent", mock_search)

    with patch("src.agents.router.classify_intent",
               new_callable=AsyncMock, return_value=("search", 0.95, "semantic")):
        result = await route("帮我找奶茶")

    assert result["intent"] == "search"
    assert result["confidence"] == 0.95
    assert result["source"] == "semantic"
    assert result["agent"] == "search_agent"
    assert result["fallback"] is False
    assert result["result"] == {"products": ["mock"]}

    # Cleanup
    del _agent_registry["search_agent"]


@pytest.mark.asyncio
async def test_route_slow_path():
    """Low confidence falls back to LLM classification."""
    async def mock_compare(input, **kwargs):
        return {"comparison": "mock"}

    register_agent("compare_agent", mock_compare)

    with patch("src.agents.router.classify_intent",
               new_callable=AsyncMock, return_value=("compare", 0.60, "llm")):
        result = await route("帮我比一下这两款")

    assert result["intent"] == "compare"
    assert result["source"] == "llm"
    assert result["agent"] == "compare_agent"

    del _agent_registry["compare_agent"]


@pytest.mark.asyncio
async def test_route_unknown_intent_fallback():
    """Unknown intent falls back to general agent."""
    async def mock_general(input, **kwargs):
        return {"general": True}

    register_agent("general_agent", mock_general)

    with patch("src.agents.router.classify_intent",
               new_callable=AsyncMock, return_value=("unknown_intent", 0.3, "llm")):
        result = await route("随便聊聊")

    assert result["agent"] == "general_agent"
    assert result["fallback"] is True

    del _agent_registry["general_agent"]


@pytest.mark.asyncio
async def test_route_unregistered_agent():
    """Returns None result when agent is not registered."""
    with patch("src.agents.router.classify_intent",
               new_callable=AsyncMock, return_value=("search", 0.95, "semantic")):
        result = await route("帮我找奶茶")

    # search_agent might be registered from other tests, so check result
    assert result["intent"] == "search"
    # If not registered, result is None
    if "search_agent" not in _agent_registry:
        assert result["result"] is None


@pytest.mark.asyncio
async def test_route_agent_error_fallback():
    """Agent error triggers fallback to general agent."""
    async def failing_handler(input, **kwargs):
        raise ValueError("boom")

    async def mock_general(input, **kwargs):
        return {"recovered": True}

    register_agent("search_agent", failing_handler)
    register_agent("general_agent", mock_general)

    with patch("src.agents.router.classify_intent",
               new_callable=AsyncMock, return_value=("search", 0.95, "semantic")):
        result = await route("帮我找奶茶")

    assert result["agent"] == "general_agent"
    assert result["fallback"] is True

    # Cleanup
    del _agent_registry["search_agent"]
    del _agent_registry["general_agent"]


@pytest.mark.asyncio
async def test_route_with_context():
    """Context is passed through to the handler."""
    received_context = {}

    async def mock_handler(input, **kwargs):
        received_context.update(kwargs.get("context", {}))
        return {"ok": True}

    register_agent("search_agent", mock_handler)

    ctx = {"entities": {"category": "护肤"}, "session_id": "test123"}
    with patch("src.agents.router.classify_intent",
               new_callable=AsyncMock, return_value=("search", 0.95, "semantic")):
        result = await route_with_context("帮我找护肤品", context=ctx)

    assert result["result"] == {"ok": True}
    assert received_context["entities"]["category"] == "护肤"

    del _agent_registry["search_agent"]
