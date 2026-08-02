"""P8 — 排序器 + 放宽链回归测试。

原文件还含一批针对旧嵌套 taxonomy(男装/上装 …)的 `_expand_category` 展开
测试;该 taxonomy 与函数已随「filter_builder 死映射清理」删除(现库为扁平品类,
检索路径由 build_filter 直接扁平硬过滤,已由 test_p02_core_filters.py 与
test_recall_relaxation.py 覆盖)。此处保留与之无关的排序器与放宽链测试。
"""

import pytest


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
