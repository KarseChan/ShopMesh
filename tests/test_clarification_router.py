"""回归测试 — 澄清答复的规则路由(不经 LLM)。

背景:线上「帮我找护肤品」→ 系统追问肤质(pending=["skin_type"])→ 用户答
「敏感肌面霜」时前端卡死。根因:`_contains_pending_field_values` 只处理
gender/product_type,**完全没有 skin_type 分支**,导致肤质答复永远匹配不到
规则、落到慢速 LLM fallback(网关限流时实测卡 ~7.5 分钟)。

修复:补 skin_type 词表 + 分支,让肤质答复走规则路由(0 LLM 调用),并顺带在
同域时捕获用户主动说出的 product_type(如「面霜」)。
"""

from src.agents.clarification_router import (
    _contains_pending_field_values,
    route_clarification,
)


class TestContainsPendingFieldValues:
    def test_skin_type_parsed(self):
        """肤质答复必须被识别(原来完全漏掉 skin_type)。"""
        parsed = _contains_pending_field_values("敏感肌面霜", ["skin_type"], {"category": "护肤"})
        assert parsed["skin_type"] == "敏感肌"
        # 同域主动说出的 product_type 也应捕获
        assert parsed["product_type"] == "面霜"

    def test_skin_type_variants(self):
        for text, expected in [("干性", "干性"), ("油皮", "油性"),
                               ("混合性皮肤", "混合性"), ("中性", "中性"),
                               ("我是敏感肌", "敏感肌")]:
            parsed = _contains_pending_field_values(text, ["skin_type"], {"category": "护肤"})
            assert parsed.get("skin_type") == expected, f"{text} → {parsed}"

    def test_cross_domain_product_type_not_captured(self):
        """肤质追问下答「手机」是跨域切换,不应被当成 product_type 答复。"""
        parsed = _contains_pending_field_values("手机", ["skin_type"], {"category": "护肤"})
        assert parsed == {}

    def test_gender_still_works(self):
        parsed = _contains_pending_field_values("男士衬衫", ["gender", "product_type"], {"category": "服饰"})
        assert parsed["gender"] == "男"
        assert parsed["product_type"] == "衬衫"


class TestRouteClarificationRulePath:
    """route_clarification 对肤质答复必须走规则(method=rule),不触发 LLM fallback。"""

    async def test_skin_type_answer_routes_via_rule(self):
        r = await route_clarification(
            "敏感肌面霜", ["skin_type"], {"category": "护肤"}, "skincare_skin_type")
        assert r["route"] == "clarification_answer"
        assert r["parsed_fields"]["skin_type"] == "敏感肌"
        assert r["parsed_fields"]["product_type"] == "面霜"
        # 规则路由的 confidence 是硬编码 0.95(非 LLM 返回),间接确认没走 LLM
        assert r["confidence"] == 0.95
