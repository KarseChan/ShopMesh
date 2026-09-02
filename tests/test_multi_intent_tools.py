"""多意图工具并集回归测试。

背景:此前 node_agent_router 用 merge_agent_configs 算了工具并集,但只把 active_agent
存进 state,_run_agent_loop 又按主 agent 名重取工具 → 次要意图的工具被静默丢掉。
修复:router 把并集写入 state["active_tools"];_run_agent_loop 用 _active_tool_names
优先取 state 里的并集。这里锁死这两点,防回归。
"""

import asyncio

from src.agents.agent_config import merge_agent_configs
from src.graph.specialized_agents import (
    _active_tool_names,
    _multi_intent_hint,
    node_agent_router,
)


class _Cfg:
    tools = ["only_own_tool"]


def test_merge_configs_unions_tools():
    """两意图 → 工具并集应同时含检索与比价类工具。"""
    cfg = merge_agent_configs(["search_recommend_agent", "detail_compare_agent"])
    assert "product_search" in cfg.tools          # 来自 search_recommend
    assert "price_compare" in cfg.tools            # 来自 detail_compare(次要意图)
    assert "product_detail_batch" in cfg.tools
    # 并集去重保序:无重复
    assert len(cfg.tools) == len(set(cfg.tools))


def test_router_puts_union_into_state():
    """多标签路由必须把并集写进 state['active_tools'](修复的核心)。"""
    state = {"user_goals": ["find_product", "compare_products"]}
    out = asyncio.run(node_agent_router(state))
    assert out["active_agent"] == "search_recommend_agent"  # 主意图(第一个)
    tools = out["active_tools"]
    assert "product_search" in tools and "price_compare" in tools, \
        "次要意图 compare_products 的 price_compare 必须在并集里,否则又退化成丢意图"


def test_router_single_intent_keeps_own_tools():
    state = {"user_goals": ["find_product"]}
    out = asyncio.run(node_agent_router(state))
    assert "product_search" in out["active_tools"]
    assert "price_compare" not in out["active_tools"]  # 单意图不引入无关工具


def test_active_tool_names_prefers_state_union():
    """执行环节优先用 state 并集,而非按名重取的单 agent 工具。"""
    # state 有并集 → 用并集
    assert _active_tool_names({"active_tools": ["a", "b"]}, _Cfg) == ["a", "b"]
    # state 无并集 → 回退 agent 自身工具(兼容无路由直调)
    assert _active_tool_names({}, _Cfg) == ["only_own_tool"]


def test_multi_intent_hint_only_when_multi():
    assert _multi_intent_hint({"user_goals": ["find_product"]}) == ""
    hint = _multi_intent_hint({"user_goals": ["find_product", "compare_products"]})
    assert "多意图" in hint and "对比商品/比价" in hint
