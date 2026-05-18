"""T5.1 Tool unit tests — schema, registry, and each tool function."""

import pytest

from src.tools.schema import ToolDef, ToolRegistry, tool_registry


# === Schema tests ===

class TestToolDef:
    def test_to_tool_schema(self):
        t = ToolDef(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        schema = t.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"
        assert schema["function"]["description"] == "A test tool"
        assert "x" in schema["function"]["parameters"]["properties"]

    def test_default_parameters(self):
        t = ToolDef(name="t", description="d")
        assert t.parameters == {}
        assert t.version == "1.0.0"
        assert t.func is None


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        t = ToolDef(name="my_tool", description="desc")
        reg.register(t)
        assert reg.get_by_name("my_tool") is t
        assert reg.get_by_name("nonexistent") is None

    def test_get_all(self):
        reg = ToolRegistry()
        reg.register(ToolDef(name="a", description="a"))
        reg.register(ToolDef(name="b", description="b"))
        assert len(reg.get_all()) == 2

    def test_get_tool_schemas(self):
        reg = ToolRegistry()
        reg.register(ToolDef(name="x", description="d"))
        schemas = reg.get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "x"

    def test_overwrite_on_same_name(self):
        reg = ToolRegistry()
        reg.register(ToolDef(name="t", description="v1"))
        reg.register(ToolDef(name="t", description="v2"))
        assert reg.get_by_name("t").description == "v2"


# === Tool function tests ===

class TestProductSearch:
    @pytest.mark.asyncio
    async def test_returns_dict_structure(self):
        from src.tools.product_search import product_search
        result = await product_search(entities={"category": "护肤"}, semantic_query="补水", top_k=3)
        assert "results" in result
        assert "total" in result
        assert "filter_applied" in result
        assert "latency_ms" in result
        assert isinstance(result["results"], list)

    @pytest.mark.asyncio
    async def test_rank_score_present(self):
        from src.tools.product_search import product_search
        result = await product_search(entities={}, semantic_query="商品", top_k=2)
        if result["results"]:
            assert "rank_score" in result["results"][0]


class TestProductDetailBatch:
    @pytest.mark.asyncio
    async def test_fetch_by_ids(self):
        from src.tools.product_detail import product_detail_batch
        result = await product_detail_batch(["prod_001", "prod_002"])
        assert isinstance(result, list)
        assert len(result) <= 2
        if result:
            assert "product_id" in result[0]

    @pytest.mark.asyncio
    async def test_empty_ids(self):
        from src.tools.product_detail import product_detail_batch
        result = await product_detail_batch([])
        assert result == []


class TestPriceCompare:
    @pytest.mark.asyncio
    async def test_compare_structure(self):
        from src.tools.product_detail import price_compare
        result = await price_compare(["prod_001", "prod_002"])
        assert "products" in result
        assert "price_range" in result
        assert "best_value" in result
        assert "min" in result["price_range"]
        assert "max" in result["price_range"]

    @pytest.mark.asyncio
    async def test_empty_ids(self):
        from src.tools.product_detail import price_compare
        result = await price_compare([])
        assert result["products"] == []
        assert result["best_value"] is None


class TestReviewSummary:
    @pytest.mark.asyncio
    async def test_summary_structure(self):
        from src.tools.review_tool import review_summary
        result = await review_summary(["prod_001"])
        assert isinstance(result, list)
        if result:
            assert "product_id" in result[0]
            assert "reputation_label" in result[0]
            assert "selling_points" in result[0]
            assert "concerns" in result[0]

    @pytest.mark.asyncio
    async def test_nonexistent_ids(self):
        from src.tools.review_tool import review_summary
        result = await review_summary(["nonexistent_999"])
        assert result == []


class TestConstraintRelaxation:
    @pytest.mark.asyncio
    async def test_relaxes_one_step(self):
        from src.tools.agent_tools import constraint_relaxation
        entities = {"category": "护肤", "brand": "兰蔻", "price_max": 500}
        result = await constraint_relaxation(entities, "结果过少")
        assert "entities" in result
        assert "relaxed" in result
        assert "steps_remaining" in result
        assert len(result["relaxed"]) == 1
        # Brand should be relaxed first
        assert result["entities"]["brand"] is None

    @pytest.mark.asyncio
    async def test_nothing_to_relax(self):
        from src.tools.agent_tools import constraint_relaxation
        entities = {"category": "护肤"}
        result = await constraint_relaxation(entities, "结果过少")
        assert result["relaxed"] == []


class TestAskClarification:
    @pytest.mark.asyncio
    async def test_returns_questions(self):
        from src.tools.agent_tools import ask_clarification
        entities = {"category": "护肤"}
        result = await ask_clarification(entities, [])
        assert "should_ask" in result
        assert "questions" in result
        assert "reason" in result

    @pytest.mark.asyncio
    async def test_already_asked_fields_skipped(self):
        from src.tools.agent_tools import ask_clarification
        entities = {"category": "护肤"}
        result = await ask_clarification(entities, ["price_max", "brand"])
        # Should not re-ask already asked fields
        asked = [q["field"] for q in result["questions"]]
        assert "price_max" not in asked
        assert "brand" not in asked


# === Registry integration test ===

class TestRegistryIntegration:
    def test_all_six_tools_registered(self):
        from src.tools.registry import get_dynamic_tools
        tools = get_dynamic_tools()
        names = {t.name for t in tools}
        assert names == {
            "product_search",
            "product_detail_batch",
            "price_compare",
            "review_summary",
            "constraint_relaxation",
            "ask_clarification",
        }

    def test_tool_schemas_valid(self):
        from src.tools.registry import get_all_tool_schemas
        schemas = get_all_tool_schemas()
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "description" in s["function"]
            assert "parameters" in s["function"]
