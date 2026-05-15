"""T2.7 Complete Shopping Graph integration tests."""

import pytest
from unittest.mock import AsyncMock, patch

from src.graph.shopping_graph import (
    build_shopping_graph,
    run_shopping,
    run_shopping_stream,
    get_latency_stats,
    _route_after_clarify,
    _latency_stats,
)


# === Routing Logic ===

def test_route_after_clarify_with_question():
    """Clarification question generated → route to clarify."""
    state = {
        "explanation": "你想找什么类型的商品？",
        "clarification_count": 1,
        "entities": {},
    }
    assert _route_after_clarify(state) == "clarify"


def test_route_after_clarify_no_question():
    """No explanation → route to retrieve."""
    state = {
        "explanation": "",
        "clarification_count": 0,
        "entities": {},
    }
    assert _route_after_clarify(state) == "retrieve"


def test_route_after_clarify_max_rounds():
    """Past max rounds → route to retrieve even if question exists."""
    state = {
        "explanation": "还想问点什么？",
        "clarification_count": 4,
        "entities": {},
    }
    assert _route_after_clarify(state) == "retrieve"


# === Graph Structure ===

def test_build_shopping_graph():
    """Graph compiles without error."""
    graph = build_shopping_graph()
    assert graph is not None
    assert hasattr(graph, "ainvoke")
    assert hasattr(graph, "astream")


# === Patch Helpers ===

# classify_intent is imported locally inside node_classify_intent from src.router.intent_classifier
# All other functions are imported at module level in src.graph.shopping_graph

_PATCHES_SUCCESS = [
    ("src.router.intent_classifier.classify_intent", {"new_callable": AsyncMock}),
    ("src.graph.shopping_graph.extract_entities", {"new_callable": AsyncMock}),
    ("src.graph.shopping_graph.recall", {"new_callable": AsyncMock}),
    ("src.graph.shopping_graph.should_recall", {}),
    ("src.graph.shopping_graph.should_clarify", {"new_callable": AsyncMock}),
    ("src.graph.shopping_graph.hybrid_search", {"new_callable": AsyncMock}),
    ("src.graph.shopping_graph.calculate_promotion", {}),
    ("src.graph.shopping_graph.rank", {}),
    ("src.graph.shopping_graph.generate_reason", {}),
    ("src.graph.shopping_graph.polish_reason", {"new_callable": AsyncMock}),
    ("src.graph.shopping_graph.write_chunk", {"new_callable": AsyncMock}),
]


def _apply_patches(patch_list):
    """Apply a list of (target, kwargs) patches and return the mocks."""
    stack = []
    mocks = {}
    for target, kwargs in patch_list:
        p = patch(target, **kwargs)
        stack.append(p)
        mocks[target] = p.start()
    return stack, mocks


def _stop_patches(stack):
    for p in reversed(stack):
        p.stop()


# === End-to-End (mocked) ===

@pytest.mark.asyncio
async def test_run_shopping_basic():
    """run_shopping returns result with explanation and latency stats."""
    stack, mocks = _apply_patches(_PATCHES_SUCCESS)
    try:
        mocks["src.router.intent_classifier.classify_intent"].return_value = ("search", 0.95, "semantic_router")
        mocks["src.graph.shopping_graph.extract_entities"].return_value = {"category": "茶饮"}
        mocks["src.graph.shopping_graph.should_recall"].return_value = False
        mocks["src.graph.shopping_graph.should_clarify"].return_value = {
            "should_ask": False, "question": None,
            "reason": "candidates_le_20", "candidates": 10,
        }
        mocks["src.graph.shopping_graph.hybrid_search"].return_value = {
            "results": [
                {"id": "p1", "payload": {"product_id": "p1", "name": "奶茶", "price": 15}, "score": 0.9},
            ]
        }
        mocks["src.graph.shopping_graph.calculate_promotion"].return_value = {
            "product_id": "p1", "final_price": 15, "promo_desc": None,
            "suggest_message": None, "is_abnormal": False,
        }
        mocks["src.graph.shopping_graph.rank"].return_value = [
            {"name": "奶茶", "price": 15, "final_price": 15, "rank_score": 0.9,
             "rank_reasons": {"relevance": 0.9, "price": 0.8}},
        ]
        mocks["src.graph.shopping_graph.generate_reason"].return_value = "推荐理由"
        mocks["src.graph.shopping_graph.polish_reason"].return_value = "润色理由"

        result = await run_shopping("帮我找一杯奶茶", session_id="test")

        assert "explanation" in result
        assert "ranked_results" in result
        assert result["intent"] == "search"
    finally:
        _stop_patches(stack)


@pytest.mark.asyncio
async def test_run_shopping_with_clarification():
    """run_shopping handles clarification path."""
    patches = [
        ("src.router.intent_classifier.classify_intent", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.extract_entities", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.recall", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.should_recall", {}),
        ("src.graph.shopping_graph.should_clarify", {"new_callable": AsyncMock}),
    ]
    stack, mocks = _apply_patches(patches)
    try:
        mocks["src.router.intent_classifier.classify_intent"].return_value = ("search", 0.6, "llm")
        mocks["src.graph.shopping_graph.extract_entities"].return_value = {"category": None}
        mocks["src.graph.shopping_graph.should_recall"].return_value = False
        mocks["src.graph.shopping_graph.should_clarify"].return_value = {
            "should_ask": True,
            "question": "你想找什么类型的商品？",
            "reason": "missing_info",
            "candidates": 100,
        }

        result = await run_shopping("帮我找个东西", session_id="test")

        assert result["clarification_count"] == 1
        assert "explanation" in result
    finally:
        _stop_patches(stack)


@pytest.mark.asyncio
async def test_run_shopping_empty_results():
    """run_shopping handles empty search results gracefully."""
    patches = [
        ("src.router.intent_classifier.classify_intent", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.extract_entities", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.recall", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.should_recall", {}),
        ("src.graph.shopping_graph.should_clarify", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.hybrid_search", {"new_callable": AsyncMock}),
        ("src.graph.shopping_graph.calculate_promotion", {}),
        ("src.graph.shopping_graph.rank", {}),
    ]
    stack, mocks = _apply_patches(patches)
    try:
        mocks["src.router.intent_classifier.classify_intent"].return_value = ("search", 0.9, "semantic_router")
        mocks["src.graph.shopping_graph.extract_entities"].return_value = {"category": "不存在的品类"}
        mocks["src.graph.shopping_graph.should_recall"].return_value = False
        mocks["src.graph.shopping_graph.should_clarify"].return_value = {
            "should_ask": False, "question": None,
            "reason": "candidates_le_20", "candidates": 0,
        }
        mocks["src.graph.shopping_graph.hybrid_search"].return_value = {"results": []}
        mocks["src.graph.shopping_graph.calculate_promotion"].return_value = {}
        mocks["src.graph.shopping_graph.rank"].return_value = []

        result = await run_shopping("找不存在的商品", session_id="test")

        assert "抱歉" in result["explanation"] or result["explanation"] == ""
    finally:
        _stop_patches(stack)


# === SSE Streaming ===

@pytest.mark.asyncio
async def test_run_shopping_stream_yields_events():
    """Streaming yields intent, entities, results, explanation, done events."""
    stack, mocks = _apply_patches(_PATCHES_SUCCESS)
    try:
        mocks["src.router.intent_classifier.classify_intent"].return_value = ("search", 0.95, "semantic_router")
        mocks["src.graph.shopping_graph.extract_entities"].return_value = {"category": "茶饮"}
        mocks["src.graph.shopping_graph.should_recall"].return_value = False
        mocks["src.graph.shopping_graph.should_clarify"].return_value = {
            "should_ask": False, "question": None,
            "reason": "candidates_le_20", "candidates": 10,
        }
        mocks["src.graph.shopping_graph.hybrid_search"].return_value = {
            "results": [
                {"id": "p1", "payload": {"product_id": "p1", "name": "奶茶", "price": 15}, "score": 0.9},
            ]
        }
        mocks["src.graph.shopping_graph.calculate_promotion"].return_value = {
            "product_id": "p1", "final_price": 15, "promo_desc": None,
            "suggest_message": None, "is_abnormal": False,
        }
        mocks["src.graph.shopping_graph.rank"].return_value = [
            {"name": "奶茶", "price": 15, "final_price": 15, "rank_score": 0.9,
             "rank_reasons": {"relevance": 0.9, "price": 0.8}},
        ]
        mocks["src.graph.shopping_graph.generate_reason"].return_value = "推荐理由"
        mocks["src.graph.shopping_graph.polish_reason"].return_value = "润色理由"

        events = []
        async for event in run_shopping_stream("帮我找一杯奶茶", session_id="test"):
            events.append(event)

        event_types = [e["event"] for e in events]
        assert "done" in event_types
        assert events[-1]["event"] == "done"
        assert "latency_ms" in events[-1]["data"]
    finally:
        _stop_patches(stack)


# === Latency Stats ===

def test_get_latency_stats_empty():
    """Empty stats return zero counts."""
    saved = {k: list(v) for k, v in _latency_stats.items()}
    _latency_stats.clear()
    _latency_stats.update({"fast": [], "standard": [], "clarification": []})

    stats = get_latency_stats()
    assert stats["fast"]["count"] == 0
    assert stats["standard"]["count"] == 0
    assert stats["clarification"]["count"] == 0

    _latency_stats.clear()
    _latency_stats.update(saved)


def test_get_latency_stats_with_data():
    """Stats with data return correct averages."""
    saved = {k: list(v) for k, v in _latency_stats.items()}
    _latency_stats.clear()
    _latency_stats.update({"fast": [1.0, 2.0], "standard": [3.0], "clarification": []})

    stats = get_latency_stats()
    assert stats["fast"]["count"] == 2
    assert stats["fast"]["avg_ms"] == 1500.0
    assert stats["standard"]["count"] == 1
    assert stats["standard"]["avg_ms"] == 3000.0

    _latency_stats.clear()
    _latency_stats.update(saved)
