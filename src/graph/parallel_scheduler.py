"""Parallel Scheduler — fan-out/fan-in execution with timeout and error degradation.

Defines parallel groups and execution rules for the DAG engine.
"""

import asyncio
from typing import Any, Callable

from src.observability.logger import get_logger

logger = get_logger("parallel_scheduler")

# Default timeout per node (seconds)
NODE_TIMEOUT = 2.0
# Global timeout for the entire graph execution
GLOBAL_TIMEOUT = 10.0


async def run_parallel(
    tasks: dict[str, Callable],
    state: dict,
    timeout: float = NODE_TIMEOUT,
) -> dict[str, Any]:
    """Execute multiple node functions in parallel with timeout.

    Args:
        tasks: {node_name: async_callable(state) -> dict}
        state: Current graph state to pass to each task
        timeout: Per-task timeout in seconds

    Returns:
        {node_name: result_or_error}
    """
    async def _run_one(name: str, fn: Callable) -> tuple[str, Any]:
        try:
            result = await asyncio.wait_for(fn(state), timeout=timeout)
            return name, result
        except asyncio.TimeoutError:
            logger.warning("node_timeout", node=name, timeout=timeout)
            return name, {"_error": f"timeout after {timeout}s", "_degraded": True}
        except Exception as e:
            logger.error("node_error", node=name, error=str(e))
            return name, {"_error": str(e), "_degraded": True}

    results = await asyncio.gather(
        *[_run_one(name, fn) for name, fn in tasks.items()]
    )

    return dict(results)


async def run_with_timeout(
    coro,
    timeout: float = GLOBAL_TIMEOUT,
    fallback: Any = None,
) -> Any:
    """Run a coroutine with a global timeout.

    Returns fallback on timeout.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("global_timeout", timeout=timeout)
        return fallback


def merge_parallel_results(results: dict[str, dict]) -> dict:
    """Merge results from parallel execution into a single state update.

    - List fields: concatenated
    - Dict fields: merged (later overwrites earlier)
    - Scalar fields: last writer wins
    - Error/degraded results are skipped for non-error fields
    """
    merged = {}
    errors = []

    for node_name, result in results.items():
        if isinstance(result, dict) and result.get("_error"):
            errors.append({"node": node_name, "error": result["_error"]})
            continue

        if isinstance(result, dict):
            for key, value in result.items():
                if key.startswith("_"):
                    continue
                if isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
                elif isinstance(value, dict):
                    merged.setdefault(key, {}).update(value)
                else:
                    merged[key] = value

    if errors:
        merged.setdefault("errors", []).extend(errors)

    return merged
