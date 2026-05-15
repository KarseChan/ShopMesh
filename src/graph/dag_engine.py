"""DAG Orchestration Engine — LangGraph StateGraph wrapper.

Provides:
- Parallel node registration (fan-out/fan-in)
- Conditional routing (if-else branches)
- Timeout per node and global
- Error degradation (agent failure → fallback path)
- Checkpointer integration
"""

import asyncio
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from src.graph.checkpointer import get_checkpointer
from src.graph.parallel_scheduler import (
    GLOBAL_TIMEOUT, NODE_TIMEOUT, merge_parallel_results, run_parallel,
    run_with_timeout,
)
from src.graph.state import ShoppingState
from src.observability.logger import get_logger

logger = get_logger("dag_engine")


class DAGEngine:
    """LangGraph DAG builder and executor with parallel execution support."""

    def __init__(self):
        self._graph = StateGraph(ShoppingState)
        self._nodes: dict[str, Callable] = {}
        self._parallel_groups: list[list[str]] = []
        self._timeout_overrides: dict[str, float] = {}

    def add_node(self, name: str, fn: Callable) -> None:
        """Register a node function."""
        self._nodes[name] = fn
        self._graph.add_node(name, fn)

    def add_parallel_group(self, *node_names: str) -> None:
        """Register a group of nodes to run in parallel (fan-out).

        These nodes will be executed concurrently via asyncio.gather.
        All nodes in the group receive the same state and their results
        are merged before proceeding.
        """
        self._parallel_groups.append(list(node_names))

    def add_edge(self, source: str, target: str) -> None:
        """Add a directed edge from source to target."""
        self._graph.add_edge(source, target)

    def add_conditional_edge(
        self, source: str, condition: Callable, path_map: dict[str, str]
    ) -> None:
        """Add a conditional edge (if-else branch).

        Args:
            source: Source node name
            condition: Function(state) -> str (returns a key in path_map)
            path_map: {condition_result: target_node}
        """
        self._graph.add_conditional_edges(source, condition, path_map)

    def set_entry_point(self, name: str) -> None:
        """Set the graph entry point."""
        self._graph.set_entry_point(name)

    def set_finish_point(self, name: str) -> None:
        """Connect a node to END."""
        self._graph.add_edge(name, END)

    def set_node_timeout(self, node_name: str, timeout: float) -> None:
        """Override the default timeout for a specific node."""
        self._timeout_overrides[node_name] = timeout

    def compile(self, checkpointer_backend: str = "memory"):
        """Compile the graph with checkpointer.

        Returns a compiled LangGraph graph ready for invocation.
        """
        checkpointer = get_checkpointer(checkpointer_backend)
        return self._graph.compile(checkpointer=checkpointer)


async def execute_node(
    name: str, fn: Callable, state: dict, timeout: float | None = None
) -> dict:
    """Execute a single node with timeout and error handling.

    Returns the node's state update, or an error dict on failure.
    """
    t = timeout or NODE_TIMEOUT
    try:
        result = await asyncio.wait_for(fn(state), timeout=t)
        logger.info("node_executed", node=name)
        return result
    except asyncio.TimeoutError:
        logger.warning("node_timeout", node=name, timeout=t)
        return {"errors": [{"node": name, "error": f"timeout after {t}s"}]}
    except Exception as e:
        logger.error("node_failed", node=name, error=str(e))
        return {"errors": [{"node": name, "error": str(e)}]}


async def execute_parallel_nodes(
    nodes: dict[str, Callable], state: dict, timeout: float = NODE_TIMEOUT
) -> dict:
    """Execute multiple nodes in parallel and merge results.

    Used for fan-out patterns (e.g., Entity Extractor ∥ Memory Retriever).
    """
    results = await run_parallel(nodes, state, timeout=timeout)
    return merge_parallel_results(results)


async def execute_graph(compiled_graph, input_state: dict) -> dict:
    """Execute a compiled graph with global timeout.

    Returns the final state.
    """
    async def _run():
        final_state = None
        async for event in compiled_graph.astream(input_state):
            final_state = event
        return final_state

    result = await run_with_timeout(_run(), timeout=GLOBAL_TIMEOUT)
    if result is None:
        return {"errors": [{"node": "graph", "error": f"global timeout after {GLOBAL_TIMEOUT}s"}]}
    return result
