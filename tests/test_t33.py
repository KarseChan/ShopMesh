"""T3.3 Basic Skills tests (search/compare/detail)."""

import pytest

from src.skills.search_skill import execute as search_execute, SKILL_DEFINITION as SEARCH_DEF
from src.skills.compare_skill import execute as compare_execute, SKILL_DEFINITION as COMPARE_DEF
from src.skills.detail_skill import execute as detail_execute, SKILL_DEFINITION as DETAIL_DEF


# === Skill Definitions ===

def test_search_skill_definition():
    assert SEARCH_DEF.name == "search_products"
    assert SEARCH_DEF.permissions == "read"


def test_compare_skill_definition():
    assert COMPARE_DEF.name == "compare_products"
    assert "product_id_a" in COMPARE_DEF.parameters["properties"]


def test_detail_skill_definition():
    assert DETAIL_DEF.name == "product_detail"
    assert "product_id" in DETAIL_DEF.parameters["properties"]


# === Search Skill ===

@pytest.mark.asyncio
async def test_search_by_keyword():
    result = await search_execute(keyword="奶茶")
    assert result["count"] > 0
    for p in result["results"]:
        assert "奶茶" in p["name"] or "奶茶" in p.get("embedding_text", "")


@pytest.mark.asyncio
async def test_search_by_category():
    result = await search_execute(category="奶茶")
    assert result["count"] > 0
    for p in result["results"]:
        assert p["category"] == "奶茶"


@pytest.mark.asyncio
async def test_search_by_max_price():
    result = await search_execute(max_price=20)
    assert result["count"] > 0
    for p in result["results"]:
        assert p["price"] <= 20


@pytest.mark.asyncio
async def test_search_with_limit():
    result = await search_execute(limit=3)
    assert result["count"] <= 3


@pytest.mark.asyncio
async def test_search_no_results():
    result = await search_execute(keyword="不存在的商品xyz")
    assert result["count"] == 0
    assert result["results"] == []


@pytest.mark.asyncio
async def test_search_returns_query():
    result = await search_execute(keyword="奶茶", category="奶茶", max_price=50)
    assert result["query"]["keyword"] == "奶茶"
    assert result["query"]["category"] == "奶茶"
    assert result["query"]["max_price"] == 50


# === Compare Skill ===

@pytest.mark.asyncio
async def test_compare_two_products():
    # Get two real product IDs
    result = await search_execute(limit=2)
    if result["count"] < 2:
        pytest.skip("Not enough products in mock data")

    pid_a = result["results"][0]["product_id"]
    pid_b = result["results"][1]["product_id"]

    comp = await compare_execute(pid_a, pid_b)
    assert "title" in comp
    assert "table" in comp
    assert "recommendation" in comp
    assert len(comp["table"]) == 5  # price, category, brand, rating, platform


@pytest.mark.asyncio
async def test_compare_product_not_found():
    result = await compare_execute("fake_id_1", "fake_id_2")
    assert "error" in result


@pytest.mark.asyncio
async def test_compare_one_not_found():
    result = await search_execute(limit=1)
    if result["count"] < 1:
        pytest.skip("No products in mock data")

    pid = result["results"][0]["product_id"]
    comp = await compare_execute(pid, "fake_id")
    assert "error" in comp


# === Detail Skill ===

@pytest.mark.asyncio
async def test_detail_found():
    result = await search_execute(limit=1)
    if result["count"] < 1:
        pytest.skip("No products in mock data")

    pid = result["results"][0]["product_id"]
    detail = await detail_execute(pid)
    assert "product" in detail
    assert detail["product"]["product_id"] == pid


@pytest.mark.asyncio
async def test_detail_not_found():
    result = await detail_execute("nonexistent_id")
    assert "error" in result


@pytest.mark.asyncio
async def test_detail_has_expected_fields():
    result = await search_execute(limit=1)
    if result["count"] < 1:
        pytest.skip("No products in mock data")

    pid = result["results"][0]["product_id"]
    detail = await detail_execute(pid)
    product = detail["product"]
    assert "name" in product
    assert "price" in product
    assert "category" in product
