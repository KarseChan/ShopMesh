"""P8 验证：衬衫搜索端到端测试

验证修复后的完整流程：
1. Entity Extractor 输出 product_type="衬衫"
2. Filter Builder 正确展开品类
3. Product Search 召回衬衫商品
4. 最终回复包含真实商品
"""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_category_expand_with_product_type():
    """filter_builder 展开 "服饰"+"衬衫" → 上装品类。"""
    from src.retrieval.filter_builder import _expand_category

    result = await _expand_category("服饰", "衬衫")
    assert "男装/上装" in result
    assert "女装/上装" in result
    assert len(result) == 2


@pytest.mark.asyncio
async def test_category_expand_with_gender_male():
    """filter_builder 展开 "服饰"+"衬衫"+gender="男" → 仅男装上装。"""
    from src.retrieval.filter_builder import _expand_category

    result = await _expand_category("服饰", "衬衫", gender="男")
    assert result == ["男装/上装"]
    assert "女装/上装" not in result


@pytest.mark.asyncio
async def test_category_expand_with_gender_female():
    """filter_builder 展开 "服饰"+"衬衫"+gender="女" → 仅女装上装。"""
    from src.retrieval.filter_builder import _expand_category

    result = await _expand_category("服饰", "衬衫", gender="女")
    assert result == ["女装/上装"]
    assert "男装/上装" not in result


@pytest.mark.asyncio
async def test_category_expand_broad_with_gender():
    """filter_builder 展开 "服饰"+gender="男" → 仅男装品类。"""
    from src.retrieval.filter_builder import _expand_category

    result = await _expand_category("服饰", None, gender="男")
    assert all(c.startswith("男装/") for c in result)
    assert len(result) >= 2


@pytest.mark.asyncio
async def test_category_expand_broad_only():
    """filter_builder 展开 "服饰" (无 product_type) → 全部服饰品类。"""
    from src.retrieval.filter_builder import _expand_category

    result = await _expand_category("服饰", None)
    assert len(result) > 2
    # 应包含男装和女装
    has_male = any(c.startswith("男装/") for c in result)
    has_female = any(c.startswith("女装/") for c in result)
    assert has_male and has_female


@pytest.mark.asyncio
async def test_build_filter_with_product_type():
    """build_filter 使用 product_type 构造 MatchAny 过滤器。"""
    from src.retrieval.filter_builder import build_filter

    entities = {
        "category": "服饰",
        "product_type": "衬衫",
        "scenario": "上班",
        "preference": "口碑好",
        "brand": None,
        "price_max": None,
    }
    f = await build_filter(entities)
    assert f is not None
    # 应该有 must 条件
    assert len(f.must) == 1
    cond = f.must[0]
    assert cond.key == "category"
    from qdrant_client.models import MatchAny
    assert isinstance(cond.match, MatchAny)
    assert "男装/上装" in cond.match.any
    assert "女装/上装" in cond.match.any


@pytest.mark.asyncio
async def test_product_search_returns_shirts():
    """product_search 在修正后应召回衬衫商品。"""
    from src.tools.product_search import product_search

    entities = {
        "category": "服饰",
        "product_type": "衬衫",
        "scenario": "上班",
        "preference": "口碑好",
        "brand": None,
        "price_max": None,
        "price_min": None,
    }

    # Mock hybrid_search to avoid Qdrant/embedding calls,
    # but simulate realistic behavior based on filter
    from src.retrieval.filter_builder import _expand_category

    expanded = await _expand_category("服饰", "衬衫")

    with patch("src.tools.product_search.hybrid_search") as mock_search:
        # Simulate Qdrant returning products matching the filter
        from src.tools.search_tool import load_products
        all_products = load_products()
        matched = [p for p in all_products if p["category"] in expanded]

        mock_search.return_value = {
            "results": [
                {"id": p["product_id"], "score": 0.8, "payload": p}
                for p in matched[:10]
            ],
            "filter_applied": True,
            "latency_ms": 50.0,
        }

        result = await product_search(entities, "适合上班穿的衬衫 口碑好 不太贵")

    assert result["total"] > 0
    # 应包含衬衫商品
    names = [r.get("name", "") for r in result["results"]]
    assert any("衬衫" in n for n in names), f"应包含衬衫商品，实际: {names}"
    print(f"\n召回 {result['total']} 件商品:")
    for r in result["results"]:
        print(f"  {r.get('product_id')}: {r.get('name')} | {r.get('category')} | ¥{r.get('price')}")


@pytest.mark.asyncio
async def test_relaxation_chain_includes_category():
    """constraint_relaxation 放宽链应包含 category 作为最后手段。"""
    from src.tools.agent_tools import constraint_relaxation, _RELAXATION_STEPS

    # 验证放宽链包含 category 和 product_type
    fields = [f for f, _ in _RELAXATION_STEPS]
    assert "product_type" in fields
    assert "category" in fields
    # category 应在最后
    assert fields.index("category") > fields.index("preference")

    # 模拟全部放宽过程
    entities = {
        "category": "服饰",
        "product_type": "衬衫",
        "scenario": "上班",
        "preference": "口碑好",
        "brand": None,
        "price_max": None,
        "price_min": None,
    }

    relaxed_entities = dict(entities)
    all_relaxed = []
    for _ in range(10):  # max 10 iterations
        result = await constraint_relaxation(relaxed_entities, "结果过少")
        relaxed_entities = result["entities"]
        all_relaxed.extend(result["relaxed"])
        if not result["relaxed"]:
            break

    # 最终所有字段都应被放宽
    assert relaxed_entities.get("category") is None
    assert relaxed_entities.get("product_type") is None
    assert relaxed_entities.get("scenario") is None
    assert relaxed_entities.get("preference") is None
    print(f"\n放宽顺序: {all_relaxed}")


# === 动态权重测试 ===

@pytest.mark.asyncio
async def test_keyword_match_score_direct_hit():
    """关键词直接命中。"""
    from src.agents.ranker import _keyword_match_score

    score, matched = _keyword_match_score("免烫", "H&M 商务免烫长袖衬衫 棉 免烫 商务")
    assert score == 1.0
    assert "免烫" in matched


@pytest.mark.asyncio
async def test_keyword_match_score_synonym():
    """同义词命中：'不容易皱' → '免烫'。"""
    from src.agents.ranker import _keyword_match_score

    score, matched = _keyword_match_score("不容易皱", "H&M 商务免烫长袖衬衫 棉 免烫 商务")
    assert score > 0  # "不容易皱" 的同义词包含 "免烫"
    assert len(matched) > 0


@pytest.mark.asyncio
async def test_keyword_match_score_miss():
    """完全不命中。"""
    from src.agents.ranker import _keyword_match_score

    score, matched = _keyword_match_score("防水冲锋衣", "H&M 商务免烫长袖衬衫 棉 免烫 商务")
    assert score == 0.0
    assert matched == []


@pytest.mark.asyncio
async def test_keyword_match_score_partial():
    """部分匹配：'夏天穿' 包含 '夏天'，应命中透气/速干等。"""
    from src.agents.ranker import _keyword_match_score

    score, matched = _keyword_match_score("夏天穿", "ONLY 日系休闲亚麻短袖衬衫 亚麻 透气 休闲 短袖")
    assert score > 0, f"应命中夏天的同义词，实际: score={score}, matched={matched}"


@pytest.mark.asyncio
async def test_score_attribute_match_with_soft_requirements():
    """软需求匹配：免烫衬衫应比普通T恤得分高。"""
    from src.agents.ranker import _score_attribute_match

    wrinkle_shirt = {
        "name": "H&M 商务免烫长袖衬衫",
        "product_type": "衬衫",
        "features": ["棉", "免烫", "商务", "长袖", "通勤"],
    }
    plain_tshirt = {
        "name": "H&M 简约纯色V领T恤",
        "product_type": "T恤",
        "features": ["棉", "V领", "简约", "百搭", "纯棉"],
    }
    soft_reqs = [{"text": "不容易皱", "type": "functional_preference", "importance": 0.9}]

    shirt_score = _score_attribute_match(wrinkle_shirt, soft_reqs)
    tshirt_score = _score_attribute_match(plain_tshirt, soft_reqs)
    assert shirt_score > tshirt_score


@pytest.mark.asyncio
async def test_select_rank_profile_default():
    """无特殊偏好 → default。"""
    from src.agents.ranker import select_rank_profile

    assert select_rank_profile({}) == "default"


@pytest.mark.asyncio
async def test_select_rank_profile_price_sensitive():
    """偏好含 '便宜' → price_sensitive。"""
    from src.agents.ranker import select_rank_profile

    entities = {"preference": "便宜实惠"}
    assert select_rank_profile(entities) == "price_sensitive"


@pytest.mark.asyncio
async def test_select_rank_profile_quality_sensitive():
    """偏好含 '口碑' → quality_sensitive。"""
    from src.agents.ranker import select_rank_profile

    entities = {"preference": "口碑好"}
    assert select_rank_profile(entities) == "quality_sensitive"


@pytest.mark.asyncio
async def test_select_rank_profile_scenario_preference():
    """多个软需求 → scenario_preference。"""
    from src.agents.ranker import select_rank_profile

    entities = {
        "soft_requirements": [
            {"text": "夏天穿", "type": "season_scene", "importance": 0.8},
            {"text": "不容易皱", "type": "functional_preference", "importance": 0.9},
        ]
    }
    assert select_rank_profile(entities) == "scenario_preference"


@pytest.mark.asyncio
async def test_rank_with_soft_requirements():
    """端到端：给定软需求 '不容易皱'，免烫衬衫排名高于普通T恤。"""
    from src.agents.ranker import rank

    products = [
        {
            "product_id": "prod_tshirt",
            "name": "H&M 简约纯色V领T恤",
            "product_type": "T恤",
            "features": ["棉", "V领", "简约", "百搭"],
            "price": 91,
            "reputation": 0.92,
            "platform_id": "jd",
        },
        {
            "product_id": "prod_shirt",
            "name": "H&M 商务免烫长袖衬衫",
            "product_type": "衬衫",
            "features": ["棉", "免烫", "商务", "长袖"],
            "price": 185,
            "reputation": 0.59,
            "platform_id": "jd",
        },
    ]
    entities = {
        "product_type": "衬衫",
        "soft_requirements": [
            {"text": "不容易皱", "type": "functional_preference", "importance": 0.9},
        ],
    }

    ranked = await rank(products, entities=entities)
    # 衬衫应该排第一（product_type_match + attribute_match 双重优势）
    assert ranked[0]["product_id"] == "prod_shirt"
