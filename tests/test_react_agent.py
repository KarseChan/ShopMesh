"""T5.2 ReAct Agent tests — mock LLM to test graph routing and tool execution."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.graph.react_node import should_continue


# === should_continue tests ===

class TestShouldContinue:
    def test_final_response_ends(self):
        state = {"final_response": "推荐结果", "iteration": 1, "tool_calls_log": []}
        assert should_continue(state) == "end"

    def test_max_iterations_fallback(self):
        state = {"final_response": None, "iteration": 5, "max_iterations": 5, "tool_calls_log": []}
        assert should_continue(state) == "fallback"

    def test_loop_detection_fallback(self):
        state = {
            "final_response": None,
            "iteration": 2,
            "max_iterations": 5,
            "tool_calls_log": [
                {"tool": "product_search", "args": {"q": "衬衫"}},
                {"tool": "product_search", "args": {"q": "衬衫"}},
            ],
        }
        assert should_continue(state) == "fallback"

    def test_different_tools_continue(self):
        state = {
            "final_response": None,
            "iteration": 2,
            "max_iterations": 5,
            "tool_calls_log": [
                {"tool": "ask_clarification", "args": {}},
                {"tool": "product_search", "args": {"q": "衬衫"}},
            ],
        }
        assert should_continue(state) == "continue"

    def test_same_tool_different_args_continue(self):
        state = {
            "final_response": None,
            "iteration": 2,
            "max_iterations": 5,
            "tool_calls_log": [
                {"tool": "product_search", "args": {"q": "衬衫"}},
                {"tool": "product_search", "args": {"q": "裤子"}},
            ],
        }
        assert should_continue(state) == "continue"

    def test_first_iteration_continue(self):
        state = {"final_response": None, "iteration": 0, "max_iterations": 5, "tool_calls_log": []}
        assert should_continue(state) == "continue"


# === ReAct node with mocked LLM ===

class TestReactNode:
    @pytest.mark.asyncio
    async def test_tool_call_returns_log(self):
        """When LLM returns a tool_call, node should execute tool and return log."""
        from src.graph.react_node import node_react_loop

        mock_response = {
            "tool_calls": [{
                "function": {
                    "name": "ask_clarification",
                    "arguments": '{"entities": {"category": "护肤"}, "asked_fields": []}',
                },
            }],
        }

        state = {
            "messages": [{"role": "user", "content": "推荐护肤品"}],
            "intent": "recommend",
            "entities": {"category": "护肤"},
            "memory_chunks": [],
            "tool_calls_log": [],
            "iteration": 0,
            "max_iterations": 5,
            "final_response": None,
        }

        with patch("src.graph.react_node.get_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            result = await node_react_loop(state)

        assert "tool_calls_log" in result
        assert len(result["tool_calls_log"]) == 1
        assert result["tool_calls_log"][0]["tool"] == "ask_clarification"
        assert result["iteration"] == 1
        assert "final_response" not in result

    @pytest.mark.asyncio
    async def test_final_answer_returns_response(self):
        """When LLM returns content (no tool_call), node should return final_response."""
        from src.graph.react_node import node_react_loop

        mock_response = {
            "content": "为你推荐以下护肤品...",
            "tool_calls": None,
        }

        state = {
            "messages": [{"role": "user", "content": "推荐护肤品"}],
            "intent": "recommend",
            "entities": {"category": "护肤"},
            "memory_chunks": [],
            "tool_calls_log": [{"tool": "product_search", "args": {}, "result": {"success": True, "data": {"results": []}}}],
            "iteration": 1,
            "max_iterations": 5,
            "final_response": None,
        }

        with patch("src.graph.react_node.get_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            mock_get_llm.return_value = mock_llm

            result = await node_react_loop(state)

        assert result["final_response"] == "为你推荐以下护肤品..."
        assert result["iteration"] == 2
        assert "tool_calls_log" not in result


# === Fallback node tests ===

class TestFallback:
    @pytest.mark.asyncio
    async def test_fallback_with_existing_results(self):
        """Fallback should reuse existing search_results."""
        from src.graph.fallback import node_fallback

        state = {
            "messages": [{"role": "user", "content": "推荐护肤品"}],
            "entities": {"category": "护肤"},
            "search_results": [
                {"payload": {"name": "商品A", "price": 100, "platform_id": "jd"}, "id": "p1", "score": 0.9},
            ],
            "intent": "recommend",
            "memory_chunks": [],
        }

        result = await node_fallback(state)

        assert "final_response" in result
        assert result["used_fallback"] is True
        assert len(result["search_results"]) > 0

    @pytest.mark.asyncio
    async def test_fallback_without_results_triggers_search(self):
        """Fallback should search if no results exist."""
        from src.graph.fallback import node_fallback

        state = {
            "messages": [{"role": "user", "content": "推荐护肤品"}],
            "entities": {"category": "护肤"},
            "search_results": [],
            "intent": "recommend",
            "memory_chunks": [],
        }

        with patch("src.graph.fallback.hybrid_search") as mock_search:
            mock_search.return_value = {
                "results": [{"payload": {"name": "商品B", "price": 200, "platform_id": "tb"}, "id": "p2", "score": 0.8}],
                "filter_applied": True,
                "latency_ms": 50,
            }
            result = await node_fallback(state)

        assert result["used_fallback"] is True
        mock_search.assert_called_once()


# === Graph construction test ===

class TestGraphConstruction:
    def test_build_graph(self):
        from src.graph.shopping_agent import build_shopping_agent_graph
        graph = build_shopping_agent_graph()
        assert graph is not None
        # Verify nodes exist
        nodes = list(graph.get_graph().nodes)
        assert "preprocess" in nodes
        assert "react_loop" in nodes
        assert "fallback" in nodes
        assert "postprocess" in nodes
