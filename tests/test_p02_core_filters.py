"""P0-2 回归测试 — 锁住三个核心链路 bug(见 reports/problem.md)。

这些测试打在确定性的过滤逻辑层(不依赖 LLM / Qdrant / Ollama),
直接喂已知的商品列表和实体,断言:

1. 预算过滤(P0):"500 以内" 绝不返回 ¥549 —— _post_filter 兜底
2. 场景适配(P1):"送女朋友生日礼物" 绝不返回奶茶/饮品 —— filter_by_scenario
3. 品类过滤(P2):品类条件真正生成到 Qdrant Filter —— build_filter

每条对应一个曾经上线的真实 bug,防止回归。
"""

from qdrant_client.models import Range

from src.agents.scenario_filter import filter_by_scenario
from src.tools.product_search import _post_filter


def _p(pid, name, category, price):
    return {"product_id": pid, "name": name, "category": category, "price": price}


# ── P0: 预算过滤 ──────────────────────────────────────────────

class TestBudgetPostFilter:
    """回归 P0:用户说'预算 500 以内'却被推 ¥549 的商品。"""

    def test_drops_over_budget(self):
        products = [_p("A", "商品A", "护肤", 499), _p("B", "商品B", "护肤", 549),
                    _p("C", "商品C", "护肤", 500)]
        kept = _post_filter(products, {"price_max": 500})
        prices = [p["price"] for p in kept]
        assert 549 not in prices, "超预算的 ¥549 必须被剔除"
        assert all(pr <= 500 for pr in prices)
        assert {p["product_id"] for p in kept} == {"A", "C"}

    def test_boundary_price_kept(self):
        """恰好等于预算上限的商品应保留(<= 而非 <)。"""
        kept = _post_filter([_p("C", "商品C", "护肤", 500)], {"price_max": 500})
        assert len(kept) == 1

    def test_drops_below_min(self):
        products = [_p("A", "低价", "护肤", 50), _p("B", "达标", "护肤", 200)]
        kept = _post_filter(products, {"price_min": 100})
        assert {p["product_id"] for p in kept} == {"B"}

    def test_price_range_both_bounds(self):
        products = [_p("A", "", "护肤", 80), _p("B", "", "护肤", 300),
                    _p("C", "", "护肤", 900)]
        kept = _post_filter(products, {"price_min": 100, "price_max": 500})
        assert {p["product_id"] for p in kept} == {"B"}

    def test_no_budget_passthrough(self):
        products = [_p("A", "", "护肤", 9999)]
        assert len(_post_filter(products, {})) == 1

    def test_missing_price_field_not_crash(self):
        """商品没有 price 字段时不应崩溃,保留(交给上游判断)。"""
        kept = _post_filter([{"product_id": "X", "category": "护肤"}], {"price_max": 500})
        assert len(kept) == 1


# ── P1: 礼物场景品类适配 ───────────────────────────────────────

class TestScenarioFilter:
    """回归 P1:用户说'送女朋友生日礼物'却被推奶茶/农夫山泉。"""

    def _gift_products(self):
        return [
            _p("SK", "雅诗兰黛精华", "护肤", 800),
            _p("PH", "iPhone", "数码", 5999),
            _p("MX", "蜜雪冰城柠檬水", "奶茶", 6),
            _p("NF", "农夫山泉", "食品", 2),
            _p("HM", "抽纸", "家居", 30),
        ]

    def test_birthday_gift_excludes_drinks_and_food(self):
        kept = filter_by_scenario(self._gift_products(), {"scenario": "送女朋友生日礼物"})
        cats = {p["category"] for p in kept}
        assert "奶茶" not in cats, "礼物场景不应推奶茶"
        assert "食品" not in cats
        assert "家居" not in cats
        assert cats <= {"护肤", "数码", "服饰", "运动"}

    def test_birthday_gift_keeps_appropriate(self):
        kept = filter_by_scenario(self._gift_products(), {"scenario": "生日礼物"})
        ids = {p["product_id"] for p in kept}
        assert "SK" in ids and "PH" in ids

    def test_no_scenario_passthrough(self):
        products = self._gift_products()
        assert len(filter_by_scenario(products, {})) == len(products)

    def test_non_gift_scenario_passthrough(self):
        """普通场景(非礼物关键词)不触发品类白名单。"""
        products = self._gift_products()
        kept = filter_by_scenario(products, {"scenario": "自己喝"})
        assert len(kept) == len(products)


class TestPostFilterCombined:
    """_post_filter 同时施加预算 + 场景(主路径实际调用形态)。"""

    def test_gift_and_budget_together(self):
        products = [
            _p("SK", "贵妇精华", "护肤", 2000),      # 场景OK但超预算
            _p("CR", "平价面霜", "护肤", 300),        # 场景OK且达标 → 唯一保留
            _p("MX", "蜜雪冰城", "奶茶", 6),          # 场景排除
        ]
        kept = _post_filter(products, {"scenario": "生日礼物", "price_max": 500})
        assert {p["product_id"] for p in kept} == {"CR"}


# ── P2: 品类过滤真正生成到 Qdrant Filter ────────────────────────

class TestBuildFilter:
    """回归 P2:品类过滤条件写反 → 检索无品类约束。"""

    async def test_category_condition_present(self):
        from src.retrieval.filter_builder import build_filter
        f = await build_filter({"category": "护肤"})
        assert f is not None, "有品类时必须生成 Filter"
        keys = [c.key for c in f.must]
        assert "category" in keys

    async def test_price_max_creates_range(self):
        from src.retrieval.filter_builder import build_filter
        f = await build_filter({"price_max": 500})
        assert f is not None
        ranges = [c for c in f.must if getattr(c, "range", None) is not None]
        assert ranges, "price_max 必须生成 Range 条件"
        assert ranges[0].range.lte == 500.0

    async def test_price_min_and_max_range(self):
        from src.retrieval.filter_builder import build_filter
        f = await build_filter({"price_min": 100, "price_max": 500})
        rng = next(c.range for c in f.must if getattr(c, "range", None) is not None)
        assert rng.gte == 100.0 and rng.lte == 500.0

    async def test_empty_entities_no_filter(self):
        from src.retrieval.filter_builder import build_filter
        assert await build_filter({}) is None
