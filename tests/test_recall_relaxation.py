"""回归测试 — Eval 缺陷闭环(见 reports/roadmap.md 剩余项 B)。

锁住两个由 Eval harness 发现的推荐质量缺陷及其修复:

1. 无锚点召回打空(礼物场景):"送女朋友的生日礼物" 无商品锚点,向量召回
   top-K 被奶茶占满,场景白名单作为**后过滤**把结果清空。
   修复:把场景品类白名单下推到 Qdrant 预过滤(build_filter),HNSW 只遍历
   礼物合适品类 → 召回非空。

2. 无解预算(硬约束不可满足):"面霜 ≤¥300",而最便宜的面霜 ¥574。
   旧行为返回空结果。修复:product_search 检测到空结果时确定性地放宽
   最不重要的约束(预算/品牌/偏好,绝不放宽 product_type/category)并重检索,
   返回带标注的最接近结果而非空。

前三类为纯确定性单测(不依赖 Qdrant/Ollama);末尾 Integration 类打全链路。
"""

from qdrant_client.models import MatchAny, MatchValue

from src.retrieval.filter_builder import build_filter
from src.tools.agent_tools import ask_clarification, constraint_relaxation


def _category_conditions(f):
    return [c for c in f.must if getattr(c, "key", None) == "category"]


# ── Fix A: 场景品类白名单下推到 Qdrant 预过滤 ──────────────────────

class TestScenarioRecallFilter:
    """回归缺陷 1:礼物场景召回打空。白名单必须进入 Qdrant Filter。"""

    async def test_birthday_gift_adds_category_whitelist(self):
        f = await build_filter({"scenario": "生日礼物", "price_max": 1000})
        assert f is not None
        cat_conds = _category_conditions(f)
        assert cat_conds, "礼物场景应生成 category 白名单条件"
        assert isinstance(cat_conds[0].match, MatchAny)
        allowed = set(cat_conds[0].match.any)
        assert allowed <= {"护肤", "数码", "服饰", "运动"}
        assert "奶茶" not in allowed and "食品" not in allowed and "家居" not in allowed

    async def test_valentine_whitelist_excludes_sports(self):
        """情人节白名单更窄(不含运动)。"""
        f = await build_filter({"scenario": "情人节", "price_max": 800})
        allowed = set(_category_conditions(f)[0].match.any)
        assert "运动" not in allowed
        assert allowed <= {"护肤", "数码", "服饰"}

    async def test_explicit_category_wins_over_scenario(self):
        """用户明确给了品类时,尊重其选择,不再叠加场景白名单。"""
        f = await build_filter({"category": "护肤", "scenario": "生日礼物"})
        cat_conds = _category_conditions(f)
        assert len(cat_conds) == 1, "只应有用户显式品类,不叠加场景白名单"
        assert isinstance(cat_conds[0].match, MatchValue)
        assert cat_conds[0].match.value == "护肤"

    async def test_non_gift_scenario_no_whitelist(self):
        """非礼物场景不触发品类白名单。"""
        f = await build_filter({"scenario": "自己用", "price_max": 500})
        assert not _category_conditions(f), "普通场景不应生成品类白名单"


# ── Fix B(核心): constraint_relaxation 的 protected_fields ─────────

class TestConstraintRelaxationProtected:
    """回归缺陷 2:无解预算应放宽预算,但绝不放宽 product_type/category。"""

    async def test_price_relaxed_identity_protected(self):
        ents = {"category": "护肤", "product_type": "面霜", "price_max": 300}
        r = await constraint_relaxation(
            ents, "结果为空", protected_fields=["product_type", "category"])
        assert r["relaxed"] == ["扩大价格上限"]
        assert r["entities"]["price_max"] is None
        # 身份约束必须原样保留 —— 不能给要面霜的人推手机
        assert r["entities"]["product_type"] == "面霜"
        assert r["entities"]["category"] == "护肤"

    async def test_protected_only_nothing_to_relax(self):
        """只剩受保护字段时,不再放宽(避免降级成无关品类)。"""
        ents = {"category": "护肤", "product_type": "面霜"}
        r = await constraint_relaxation(
            ents, "结果为空", protected_fields=["product_type", "category"])
        assert r["relaxed"] == []
        assert r["steps_remaining"] == 0

    async def test_default_behavior_unchanged(self):
        """不传 protected_fields 时行为与旧版一致(向后兼容)。"""
        ents = {"category": "护肤", "brand": "兰蔻", "price_max": 500}
        r = await constraint_relaxation(ents, "结果过少")
        assert r["entities"]["brand"] is None  # 品牌最先放宽
        assert len(r["relaxed"]) == 1


# ── 附带修复: ask_clarification search_failed 分支的 NameError ──────

class TestAskClarificationSearchFailed:
    """回归:search_failed 分支曾引用未定义的 `brand` → NameError。"""

    async def test_search_failed_does_not_crash(self):
        r = await ask_clarification(
            {"brand": "兰蔻", "category": "护肤", "price_max": 300},
            [], search_failed=True)
        assert r["should_ask"] is True
        assert r["question_type"] == "search_failed_relax"
        # brand 应被正确纳入约束描述
        assert "兰蔻" in r["question_spec"]["context"]


# ── Integration: product_search 全链路(需 Qdrant + Ollama) ─────────

class TestProductSearchRelaxationIntegration:
    """全链路断言修复后的用户可见行为。依赖向量库 + embedding 服务。"""

    async def test_unsolvable_budget_recovers_with_relaxation(self):
        from src.tools.product_search import product_search
        r = await product_search(
            {"category": "护肤", "product_type": "面霜", "price_max": 300},
            "保湿面霜", top_k=30, max_results=10)
        prods = r["_full_products"]
        assert prods, "无解预算不应返回空,应放宽后给最接近结果"
        assert r["relaxed"] is True
        assert "扩大价格上限" in r["relaxed_constraints"]
        assert r["relaxation_note"], "放宽必须带用户可见说明"
        # 身份约束保持:仍是面霜,仍是护肤
        assert all(p.get("product_type") == "面霜" for p in prods)
        assert all(p.get("category") == "护肤" for p in prods)
        # 放宽预算后应给最便宜的替代(升序),而非最贵的
        prices = [p["price"] for p in prods]
        assert prices == sorted(prices), "放宽预算后应价格升序(最接近预算)"

    async def test_gift_scenario_recall_non_empty(self):
        from src.tools.product_search import product_search
        r = await product_search(
            {"scenario": "生日礼物", "price_max": 1000},
            "送女朋友的生日礼物", top_k=30, max_results=10)
        prods = r["_full_products"]
        assert prods, "礼物场景召回不应打空"
        assert r["relaxed"] is False, "召回已修复,不应触发放宽"
        cats = {p.get("category") for p in prods}
        assert cats <= {"护肤", "数码", "服饰", "运动"}
        assert all(p["price"] <= 1000 for p in prods)


# ── 放宽提示的确定性回传(不依赖 LLM) ─────────────────────────────

class TestRelaxationNoteSurfacing:
    """放宽结果必须带用户可见说明,而非依赖 LLM 主动提及(否则超预算结果
    看起来像 P0 预算 bug 回归)。extract_relaxation_note 从 tool_log 里
    确定性取出提示,在响应汇总前置。"""

    def test_note_extracted_from_relaxed_search(self):
        from src.graph.stream_utils import extract_relaxation_note
        # tool_executor 包装形态: {"success": True, "data": <product_search 返回>}
        tool_log = [{
            "tool": "product_search",
            "result": {"success": True, "data": {
                "results": [{"product_id": "x"}],
                "relaxed": True,
                "relaxed_constraints": ["扩大价格上限"],
                "relaxation_note": "没有完全符合条件的商品，已为你放宽：扩大价格上限，以下是最接近的结果。",
            }},
        }]
        note = extract_relaxation_note(tool_log)
        assert "放宽" in note and "扩大价格上限" in note

    def test_no_note_when_not_relaxed(self):
        from src.graph.stream_utils import extract_relaxation_note
        tool_log = [{
            "tool": "product_search",
            "result": {"success": True, "data": {
                "results": [{"product_id": "x"}], "relaxed": False, "relaxation_note": "",
            }},
        }]
        assert extract_relaxation_note(tool_log) == ""

    def test_empty_log_no_note(self):
        from src.graph.stream_utils import extract_relaxation_note
        assert extract_relaxation_note([]) == ""
