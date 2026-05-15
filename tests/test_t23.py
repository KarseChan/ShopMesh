"""T2.3 Hybrid Retriever tests."""

import pytest

from src.retrieval.filter_builder import build_filter, build_filter_from_keywords


# === Filter Builder Tests ===

def test_build_filter_category():
    entities = {"category": "护肤"}
    f = build_filter(entities)
    assert f is not None
    assert len(f.must) == 1


def test_build_filter_price_range():
    entities = {"price_max": 100}
    f = build_filter(entities)
    assert f is not None
    assert len(f.must) == 1
    # Check it's a range condition
    cond = f.must[0]
    assert cond.key == "price"


def test_build_filter_combined():
    entities = {"category": "奶茶", "price_max": 20}
    f = build_filter(entities)
    assert f is not None
    assert len(f.must) == 2


def test_build_filter_brand():
    entities = {"brand": "Apple"}
    f = build_filter(entities)
    assert f is not None
    assert len(f.must) == 1


def test_build_filter_price_min_max():
    entities = {"price_min": 100, "price_max": 500}
    f = build_filter(entities)
    assert f is not None
    cond = f.must[0]
    assert cond.key == "price"
    assert cond.range.gte == 100.0
    assert cond.range.lte == 500.0


def test_build_filter_no_conditions():
    entities = {"scenario": "自用"}  # scenario is not filterable
    f = build_filter(entities)
    assert f is None


def test_build_filter_empty():
    assert build_filter({}) is None


def test_build_filter_platform():
    entities = {"platform_id": "jd"}
    f = build_filter(entities)
    assert f is not None
    assert f.must[0].key == "platform_id"


def test_build_filter_multiple_categories():
    entities = {"categories": ["护肤", "数码"]}
    f = build_filter(entities)
    assert f is not None


def test_build_filter_from_keywords():
    """Keywords-based filter returns None (relies on vector search)."""
    f = build_filter_from_keywords(["奶茶", "古茗"])
    assert f is None


# === Hybrid Search Tests (mocked) ===

@pytest.mark.asyncio
async def test_hybrid_search_returns_results():
    """Hybrid search returns results from Qdrant."""
    from src.retrieval.hybrid_retriever import hybrid_search

    # This test requires Qdrant to be running
    try:
        result = await hybrid_search(
            query="奶茶",
            entities={"category": "奶茶", "price_max": 50},
            top_k=5,
        )
        assert "results" in result
        assert "filter_applied" in result
        assert "latency_ms" in result
        assert result["filter_applied"] is True
    except Exception as e:
        if "Connection refused" in str(e) or "ConnectError" in str(e):
            pytest.skip("Qdrant not running")
        raise


@pytest.mark.asyncio
async def test_mock_platform_search():
    """Mock platform search returns results with latency."""
    from src.retrieval.hybrid_retriever import mock_platform_search

    result = await mock_platform_search("jd", "奶茶", {"category": "奶茶"})
    assert result["platform"] == "jd"
    assert result["latency_ms"] > 0
    assert isinstance(result["results"], list)


@pytest.mark.asyncio
async def test_mock_platform_unknown():
    """Unknown platform returns error."""
    from src.retrieval.hybrid_retriever import mock_platform_search

    result = await mock_platform_search("unknown", "test", {})
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_multi_platform_search():
    """Multi-platform search runs in parallel."""
    from src.retrieval.hybrid_retriever import multi_platform_search

    results = await multi_platform_search("奶茶", {"category": "奶茶"})
    assert len(results) == 3  # jd, tb, pdd
    platforms = {r["platform"] for r in results}
    assert platforms == {"jd", "tb", "pdd"}
