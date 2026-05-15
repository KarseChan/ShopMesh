"""T0.4 shopping graph tests."""

import pytest

from src.tools.search_tool import search_products


def test_search_by_category():
    results = search_products(category="奶茶", limit=3)
    assert len(results) > 0
    for r in results:
        assert r["category"] == "奶茶"


def test_search_by_price():
    results = search_products(max_price=20, limit=5)
    for r in results:
        assert r["price"] <= 20


def test_search_by_keyword():
    results = search_products(keyword="芋泥", limit=5)
    for r in results:
        assert "芋泥" in r["name"] or "芋泥" in r.get("embedding_text", "")


def test_search_returns_sorted():
    results = search_products(category="奶茶", limit=10)
    prices = [r["price"] for r in results]
    assert prices == sorted(prices)
