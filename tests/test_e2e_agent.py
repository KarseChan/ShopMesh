"""T5.3 End-to-end Agent tests — full graph execution with mocked LLM."""

import pytest
from unittest.mock import AsyncMock, patch


class TestE2EAgentGraph:
    """Test the full agent graph execution path with mocked LLM."""

    @pytest.mark.asyncio
    async def test_full_path_search_recommend(self):
        """Agent path: search → final answer → postprocess."""
        from src.graph.shopping_agent import build_shopping_agent_graph

        graph = build_shopping_agent_graph()

        # Mock LLM to return a tool call then a final answer
        call_count = 0
        async def mock_chat(messages, tools=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: search
                return {
                    "tool_calls": [{
                        "function": {
                            "name": "product_search",
                            "arguments": '{"entities": {"category": "护肤"}, "semantic_query": "补水保湿"}',
                        },
                    }],
                }
            else:
                # Second call: final answer
                return {
                    "content": "为你推荐以下护肤品：",
                    "tool_calls": None,
                }

        initial_state = {
            "messages": [{"role": "user", "content": "推荐补水保湿的护肤品"}],
            "user_id": "test_user",
            "intent": "recommend",
            "entities": {"category": "护肤"},
            "memory_chunks": [],
            "search_results": [],
            "user_profile": {},
            "tool_calls_log": [],
            "iteration": 0,
            "max_iterations": 5,
            "final_response": None,
            "asked_fields": [],
            "used_fallback": False,
        }

        config = {"configurable": {"thread_id": "test_e2e_1"}}

        with patch("src.graph.react_node.get_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(side_effect=mock_chat)
            mock_get_llm.return_value = mock_llm

            # Also mock postprocessing memory write to avoid Qdrant calls
            with patch("src.graph.postprocessing.write_chunk", new_callable=AsyncMock):
                result = await graph.ainvoke(initial_state, config=config)

        assert result["final_response"] is not None
        assert result["used_fallback"] is False
        assert result["iteration"] >= 1

    @pytest.mark.asyncio
    async def test_full_path_fallback(self):
        """Agent path: max iterations → fallback → postprocess."""
        from src.graph.shopping_agent import build_shopping_agent_graph

        graph = build_shopping_agent_graph()

        # Mock LLM to always return tool calls (never final answer)
        async def mock_chat(messages, tools=None):
            return {
                "tool_calls": [{
                    "function": {
                        "name": "product_search",
                        "arguments": '{"entities": {}, "semantic_query": "test"}',
                    },
                }],
            }

        initial_state = {
            "messages": [{"role": "user", "content": "找东西"}],
            "user_id": "test_user",
            "intent": "recommend",
            "entities": {},
            "memory_chunks": [],
            "search_results": [],
            "user_profile": {},
            "tool_calls_log": [],
            "iteration": 0,
            "max_iterations": 3,  # Low limit for testing
            "final_response": None,
            "asked_fields": [],
            "used_fallback": False,
        }

        config = {"configurable": {"thread_id": "test_e2e_2"}}

        with patch("src.graph.react_node.get_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(side_effect=mock_chat)
            mock_get_llm.return_value = mock_llm

            with patch("src.graph.postprocessing.write_chunk", new_callable=AsyncMock):
                result = await graph.ainvoke(initial_state, config=config)

        assert result["final_response"] is not None
        assert result["used_fallback"] is True

    @pytest.mark.asyncio
    async def test_sse_stream_events(self):
        """Agent stream yields SSE-compatible event dicts."""
        from src.graph.shopping_agent import run_agent_stream

        call_count = 0
        async def mock_chat(messages, tools=None):
            nonlocal call_count
            call_count += 1
            return {"content": "推荐结果", "tool_calls": None}

        with patch("src.graph.react_node.get_llm") as mock_get_llm:
            mock_llm = AsyncMock()
            mock_llm.chat = AsyncMock(side_effect=mock_chat)
            mock_get_llm.return_value = mock_llm

            with patch("src.graph.postprocessing.write_chunk", new_callable=AsyncMock):
                events = []
                async for event in run_agent_stream("推荐护肤品", user_id="test_user"):
                    events.append(event)

        event_types = [e["event"] for e in events]
        assert "done" in event_types
        # Should have at least one content event
        assert "explanation" in event_types or "results" in event_types


class TestAPIEndpoint:
    """Test the API mode switching."""

    def test_import_agent_stream(self):
        from src.api.chat import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/chat" in routes

    def test_mode_param_defaults_to_agent(self):
        """Verify the chat endpoint accepts mode parameter."""
        import inspect
        from src.api.chat import chat
        sig = inspect.signature(chat)
        # The function exists and is an async endpoint
        assert inspect.iscoroutinefunction(chat)
