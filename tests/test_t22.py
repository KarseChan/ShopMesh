"""T2.2 DAG Engine tests."""

import asyncio
import sys
import pytest

from langgraph.graph import END

from src.graph.state import ShoppingState, _add_lists
from src.graph.checkpointer import get_checkpointer, clear_checkpointer
from src.graph.parallel_scheduler import run_parallel, merge_parallel_results, run_with_timeout
from src.graph.dag_engine import DAGEngine, execute_node, execute_parallel_nodes


# === State Tests ===

def test_state_has_all_fields():
    """ShoppingState has all required fields via type hints."""
    hints = ShoppingState.__annotations__
    assert "messages" in hints
    assert "tool_calls" in hints
    assert "errors" in hints
    assert "intent" in hints
    assert "entities" in hints
    assert "memory_chunks" in hints
    assert "search_results" in hints
    assert "promotion_info" in hints
    assert "ranked_results" in hints
    assert "explanation" in hints
    assert "clarification_count" in hints


def test_add_lists_reducer():
    """Custom _add_lists reducer concatenates lists."""
    assert _add_lists([1, 2], [3, 4]) == [1, 2, 3, 4]
    assert _add_lists([], [1]) == [1]
    assert _add_lists([], []) == []


# === Checkpointer Tests ===

def test_get_checkpointer_memory():
    clear_checkpointer()
    cp = get_checkpointer("memory")
    assert cp is not None


def test_get_checkpointer_singleton():
    clear_checkpointer()
    cp1 = get_checkpointer("memory")
    cp2 = get_checkpointer("memory")
    assert cp1 is cp2


def test_get_checkpointer_unsupported():
    clear_checkpointer()
    with pytest.raises(ValueError, match="Unsupported"):
        get_checkpointer("redis")


# === Parallel Scheduler Tests ===

@pytest.mark.asyncio
async def test_run_parallel_basic():
    """Parallel tasks execute concurrently and return results."""
    async def task_a(state):
        await asyncio.sleep(0.05)
        return {"result_a": 1}

    async def task_b(state):
        await asyncio.sleep(0.05)
        return {"result_b": 2}

    results = await run_parallel({"a": task_a, "b": task_b}, {}, timeout=1.0)
    assert "a" in results
    assert "b" in results
    assert results["a"]["result_a"] == 1
    assert results["b"]["result_b"] == 2


@pytest.mark.asyncio
async def test_run_parallel_timeout():
    """Slow tasks get timeout error."""
    async def slow_task(state):
        await asyncio.sleep(5)
        return {"never": True}

    results = await run_parallel({"slow": slow_task}, {}, timeout=0.1)
    assert results["slow"]["_error"]
    assert results["slow"]["_degraded"] is True


@pytest.mark.asyncio
async def test_run_parallel_error():
    """Failing tasks get error result."""
    async def failing_task(state):
        raise ValueError("boom")

    results = await run_parallel({"fail": failing_task}, {}, timeout=1.0)
    assert results["fail"]["_error"]
    assert results["fail"]["_degraded"] is True


def test_merge_parallel_results():
    """Merge results from parallel execution."""
    results = {
        "node_a": {"entities": {"category": "护肤"}, "search_results": [1, 2]},
        "node_b": {"memory_chunks": ["mem1"], "search_results": [3]},
    }
    merged = merge_parallel_results(results)
    assert merged["entities"]["category"] == "护肤"
    assert merged["memory_chunks"] == ["mem1"]
    assert merged["search_results"] == [1, 2, 3]  # lists concatenated


def test_merge_parallel_results_with_errors():
    """Error results are collected in errors field."""
    results = {
        "node_a": {"entities": {"brand": "Apple"}},
        "node_b": {"_error": "timeout", "_degraded": True},
    }
    merged = merge_parallel_results(results)
    assert merged["entities"]["brand"] == "Apple"
    assert len(merged["errors"]) == 1
    assert merged["errors"][0]["node"] == "node_b"


@pytest.mark.asyncio
async def test_run_with_timeout():
    """Returns result when within timeout."""
    async def fast():
        return 42

    result = await run_with_timeout(fast(), timeout=1.0)
    assert result == 42


@pytest.mark.asyncio
async def test_run_with_timeout_exceeded():
    """Returns fallback on timeout."""
    async def slow():
        await asyncio.sleep(5)

    result = await run_with_timeout(slow(), timeout=0.1, fallback="timeout")
    assert result == "timeout"


# === DAG Engine Tests ===

def test_dag_engine_add_node():
    """DAGEngine registers nodes."""
    engine = DAGEngine()

    async def my_node(state):
        return {}

    engine.add_node("test", my_node)
    assert "test" in engine._nodes


def test_dag_engine_compile():
    """DAGEngine compiles a simple graph."""
    engine = DAGEngine()

    async def node_a(state):
        return {"intent": "search"}

    engine.add_node("a", node_a)
    engine.set_entry_point("a")
    engine.set_finish_point("a")

    compiled = engine.compile(checkpointer_backend="memory")
    assert compiled is not None


@pytest.mark.asyncio
async def test_execute_node_success():
    """execute_node runs a node and returns result."""
    async def my_node(state):
        return {"intent": "search"}

    result = await execute_node("test", my_node, {})
    assert result["intent"] == "search"


@pytest.mark.asyncio
async def test_execute_node_timeout():
    """execute_node handles timeout."""
    async def slow_node(state):
        await asyncio.sleep(5)

    result = await execute_node("test", slow_node, {}, timeout=0.1)
    assert "errors" in result


@pytest.mark.asyncio
async def test_execute_node_error():
    """execute_node handles exceptions."""
    async def failing_node(state):
        raise ValueError("boom")

    result = await execute_node("test", failing_node, {})
    assert "errors" in result


@pytest.mark.asyncio
async def test_execute_parallel_nodes():
    """execute_parallel_nodes runs nodes concurrently."""
    async def node_a(state):
        await asyncio.sleep(0.05)
        return {"intent": "search"}

    async def node_b(state):
        await asyncio.sleep(0.05)
        return {"entities": {"category": "护肤"}}

    result = await execute_parallel_nodes(
        {"a": node_a, "b": node_b}, {}, timeout=1.0
    )
    assert result["intent"] == "search"
    assert result["entities"]["category"] == "护肤"


def test_dag_engine_conditional_edge():
    """DAGEngine supports conditional edges."""
    engine = DAGEngine()

    async def classify(state):
        return {}

    async def search(state):
        return {}

    async def recommend(state):
        return {}

    engine.add_node("classify", classify)
    engine.add_node("search", search)
    engine.add_node("recommend", recommend)
    engine.set_entry_point("classify")

    def route(state):
        return state.get("intent", "search")

    engine.add_conditional_edge("classify", route, {
        "search": "search",
        "recommend": "recommend",
    })
    engine.add_edge("search", END)
    engine.add_edge("recommend", END)


def test_state_volume_estimate():
    """Verify a minimal state stays under 10KB."""
    import json
    state = {
        "messages": [{"role": "user", "content": "帮我找奶茶"}] * 10,
        "tool_calls": [],
        "errors": [],
        "intent": "search",
        "entities": {"category": "奶茶", "price_max": 20},
        "memory_chunks": [],
        "search_results": [{"name": "古茗奶茶", "price": 15}] * 5,
        "promotion_info": {},
        "ranked_results": [],
        "explanation": "为你找到以下推荐",
        "clarification_count": 0,
    }
    size = sys.getsizeof(json.dumps(state, ensure_ascii=False))
    assert size < 10240, f"State size {size} bytes exceeds 10KB"
