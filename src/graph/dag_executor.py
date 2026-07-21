"""DAG Executor — executes task DAG with dependency-aware parallelism.

Phase 1 (MVP): sequential execution by topological order.
Phase 2: asyncio.gather parallelism for independent tasks.

Each task is either:
- type:tool → direct tool call (no LLM)
- type:agent → ReAct loop with tool subset

P1-1: Integrated TaskStore for claim/complete semantics and cross-session persistence.
"""

import asyncio

from src.agents.task_templates import get_template
from src.graph.tool_executor import execute_tool
from src.observability.logger import get_logger

logger = get_logger("dag_executor")


# ──────────────────────────────────────────────
# Topological Sort
# ──────────────────────────────────────────────

def topological_sort(dag: list[dict]) -> list[list[dict]]:
    """Topological sort into layers for parallel execution.

    Returns list of layers. Tasks in the same layer have no dependencies
    on each other and can run in parallel.

    Raises ValueError on cycle detection.
    """
    # Build adjacency and in-degree
    task_map: dict[str, dict] = {}
    in_degree: dict[str, int] = {}
    dependents: dict[str, list[str]] = {}  # reverse edges

    for task in dag:
        tid = task["task_id"]
        task_map[tid] = task
        in_degree[tid] = len(task.get("depends_on", []))
        dependents.setdefault(tid, [])

    # Build reverse edges
    for task in dag:
        for dep in task.get("depends_on", []):
            dependents.setdefault(dep, []).append(task["task_id"])

    # Kahn's algorithm with layer tracking
    layers: list[list[dict]] = []
    ready = [tid for tid, deg in in_degree.items() if deg == 0]

    while ready:
        layer = [task_map[tid] for tid in ready]
        layers.append(layer)

        next_ready = []
        for tid in ready:
            for dependent in dependents.get(tid, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_ready.append(dependent)
        ready = next_ready

    # Check for cycles
    processed = sum(len(layer) for layer in layers)
    if processed != len(dag):
        raise ValueError("DAG contains a cycle")

    return layers


# ──────────────────────────────────────────────
# Task Execution
# ──────────────────────────────────────────────

async def _execute_tool_task(task: dict, task_args: dict) -> dict:
    """Execute a type:tool task by directly calling the tool."""
    tmpl = get_template(task["task_id"])
    if not tmpl or not tmpl.tool:
        return {"success": False, "error": f"No tool for task {task['task_id']}"}

    result = await execute_tool(tmpl.tool, task_args)
    return result


async def _execute_agent_task(
    task: dict,
    task_args: dict,
    state: dict,
    prior_results: dict,
) -> dict:
    """Execute a type:agent task with isolated state (Subagent pattern).

    Isolation rules (from s06_subagent):
    1. Subagent receives only essential fields, not full parent state
    2. 30-turn safety limit to prevent infinite loops
    3. Only final summary is returned, intermediate state is discarded
    4. No recursive agent spawning (enforced by tool subset)
    """
    from src.agents.agent_config import get_agent_config
    from src.graph.specialized_agents import _run_agent_loop

    tmpl = get_template(task["task_id"])
    if not tmpl or not tmpl.agent:
        return {"success": False, "error": f"No agent for task {task['task_id']}"}

    agent_name = tmpl.agent
    _MAX_SUBAGENT_TURNS = 30  # Safety limit for subagent

    # ── Build isolated state (NOT **state) ──
    # Only pass essential fields to prevent state pollution
    agent_state = {
        # Context: recent messages only (last 3 user messages)
        "messages": _extract_recent_messages(state.get("messages", []), max_count=3),
        # Entities: read-only copy
        "entities": dict(state.get("entities", {})),
        # User/session identifiers
        "user_id": state.get("user_id", "default_user"),
        "session_id": state.get("session_id", ""),
        # Prior task results for context injection
        "_prior_task_results": prior_results,
        "_task_args": task_args,
        # Agent execution state (fresh for each subagent)
        "iteration": 0,
        "max_iterations": min(state.get("max_iterations", 5), 10),  # Cap at 10
        "tool_calls_log": [],
        # Memory context (read-only)
        "memory_chunks": list(state.get("memory_chunks", [])),
        "user_profile": dict(state.get("user_profile", {})),
    }

    logger.info("subagent_isolated", agent=agent_name, task_id=task["task_id"],
                original_msg_count=len(state.get("messages", [])),
                isolated_msg_count=len(agent_state["messages"]))

    try:
        result = None
        for iteration in range(_MAX_SUBAGENT_TURNS):
            logger.info("agent_loop_iteration", agent=agent_name,
                        task_id=task["task_id"], iteration=iteration)
            try:
                result = await _run_agent_loop(agent_state, agent_name)
            except Exception as loop_err:
                logger.error("agent_loop_error", agent=agent_name,
                             task_id=task["task_id"], iteration=iteration,
                             error=str(loop_err))
                raise

            # If result has final_response, agent is done
            if "final_response" in result:
                logger.info("agent_loop_done", agent=agent_name,
                            task_id=task["task_id"], iteration=iteration)
                # Return only the final result, not the agent_state
                return {"success": True, "data": result}

            # Otherwise, agent made a tool call — update isolated state and continue
            if "tool_calls_log" in result:
                agent_state["tool_calls_log"] = (
                    agent_state.get("tool_calls_log", []) + result["tool_calls_log"]
                )
                agent_state["iteration"] = result.get("iteration", iteration + 1)
                logger.info("agent_loop_continue", agent=agent_name,
                            task_id=task["task_id"], iteration=iteration,
                            tool=result["tool_calls_log"][-1].get("tool", ""))

        # Safety limit reached
        logger.warning("subagent_max_turns", agent=agent_name, task_id=task["task_id"],
                       max_turns=_MAX_SUBAGENT_TURNS)
        return {"success": True, "data": result or {"final_response": "任务执行超时，请重试。"}}

    except Exception as e:
        logger.error("agent_task_failed", task_id=task["task_id"],
                     agent=agent_name, error=str(e))
        return {"success": False, "error": str(e)}


def _extract_recent_messages(messages: list[dict], max_count: int = 3) -> list[dict]:
    """Extract recent user messages for subagent context.

    Keeps system prompt (if present) + last N user messages.
    Discards assistant responses and tool call history to prevent context pollution.
    """
    if not messages:
        return []

    result = []
    user_count = 0

    # Iterate in reverse to get most recent user messages
    for msg in reversed(messages):
        # Handle both dict and LangChain message objects
        if isinstance(msg, dict):
            role = msg.get("role", "")
        else:
            role = getattr(msg, "type", "")  # HumanMessage.type == "human"
        if role in ("user", "human"):
            user_count += 1
            if user_count <= max_count:
                result.insert(0, msg)
        elif role in ("system",):
            result.insert(0, msg)
            break

    return result


def _extract_result_summary(result: dict) -> str:
    """Extract a short summary from task result for TaskStore."""
    if not result.get("success", False):
        return result.get("error", "Failed")[:200]

    data = result.get("data", {})
    if isinstance(data, dict):
        # For agent tasks with final_response
        if "final_response" in data:
            return data["final_response"][:200]
        # For tool tasks with results
        if "results" in data or "data" in data:
            products = data.get("results") or data.get("data")
            if isinstance(products, list):
                return f"{len(products)} products found"

    return "Completed"


def _collect_dep_results(
    task: dict,
    task_results: dict[str, dict],
) -> dict:
    """Collect results from dependency tasks for injection."""
    dep_results = {}
    for dep_id in task.get("depends_on", []):
        if dep_id in task_results:
            dep_results[dep_id] = task_results[dep_id]
    return dep_results


def _merge_task_args(task: dict, dep_results: dict, state: dict) -> dict:
    """Merge task template args with dependency results.

    For tool tasks: inject product_ids from dependency results if needed.
    For agent tasks: pass dependency results for context.
    """
    args = dict(task.get("args", {}))
    tmpl = get_template(task["task_id"])
    if not tmpl:
        return args

    # For price_check: inject product_ids from history_lookup result
    if tmpl.tool == "price_compare" and not args.get("product_ids"):
        for dep_id, dep_result in dep_results.items():
            data = dep_result.get("data", {})
            if isinstance(data, dict) and data.get("success"):
                products = data.get("data", [])
                if isinstance(products, list):
                    args["product_ids"] = [
                        p.get("product_id", "") for p in products if p.get("product_id")
                    ]

    # For review_summary: inject product_ids from dependencies
    if tmpl.tool == "review_summary" and not args.get("product_ids"):
        for dep_id, dep_result in dep_results.items():
            data = dep_result.get("data", {})
            if isinstance(data, dict) and data.get("success"):
                products = data.get("data", [])
                if isinstance(products, list):
                    args["product_ids"] = [
                        p.get("product_id", "") for p in products if p.get("product_id")
                    ]

    return args


# ──────────────────────────────────────────────
# DAG Execution Node
# ──────────────────────────────────────────────

async def node_dag_executor(state: dict) -> dict:
    """Execute the task DAG from orchestrator.

    Reads task_dag from state, executes tasks in topological order,
    collects results into task_results.

    P1-1: Uses TaskStore for claim/complete semantics when dag_id is present.

    Returns dict to merge into AgentState:
        task_results: dict[str, dict] — results keyed by task_id
        final_response: str — merged response from all agent tasks
        search_results: list — merged search results
        recommendations: list — merged recommendations
        response_type: str — from the last agent task
    """
    dag = state.get("task_dag", [])
    if not dag:
        logger.warning("dag_executor_empty_dag")
        return {"task_results": {}, "final_response": "抱歉，任务规划为空。"}

    # Initialize TaskStore if dag_id is present (P1-1 persistence)
    dag_id = state.get("dag_id")
    session_id = state.get("session_id", state.get("user_id", "default_user"))
    task_store = None
    if dag_id:
        try:
            from src.graph.task_store import get_task_store
            task_store = get_task_store()
        except Exception as e:
            logger.warning("task_store_init_failed", error=str(e))

    try:
        layers = topological_sort(dag)
    except ValueError as e:
        logger.error("dag_executor_sort_error", error=str(e))
        return {"task_results": {}, "final_response": "抱歉，任务规划出现错误。"}

    logger.info("dag_executor_start",
                 task_count=len(dag),
                 layer_count=len(layers),
                 layers=[[t["task_id"] for t in layer] for layer in layers])

    task_results: dict[str, dict] = {}

    for layer_idx, layer in enumerate(layers):
        logger.info("dag_executor_layer",
                     layer=layer_idx,
                     tasks=[t["task_id"] for t in layer])

        # Execute tasks in parallel within each layer
        tasks_coroutines = []
        task_order = []  # track which coroutine maps to which task

        for task in layer:
            tid = task["task_id"]
            tmpl = get_template(tid)
            if not tmpl:
                logger.error("dag_executor_unknown_template", task_id=tid)
                task_results[tid] = {"success": False, "error": f"Unknown template: {tid}"}
                continue

            # Claim task if TaskStore is available (P1-1)
            if task_store:
                claimed = await task_store.claim_task(session_id, dag_id, tid)
                if not claimed:
                    logger.info("dag_executor_task_blocked", task_id=tid)
                    task_results[tid] = {
                        "success": False,
                        "error": f"Task {tid} blocked by dependencies"
                    }
                    continue

            dep_results = _collect_dep_results(task, task_results)
            merged_args = _merge_task_args(task, dep_results, state)

            if tmpl.type == "tool":
                tasks_coroutines.append(_execute_tool_task(task, merged_args))
            else:
                tasks_coroutines.append(
                    _execute_agent_task(task, merged_args, state, task_results)
                )
            task_order.append(tid)

        # Run all tasks in this layer concurrently
        if tasks_coroutines:
            results = await asyncio.gather(*tasks_coroutines, return_exceptions=True)

            for tid, result in zip(task_order, results):
                if isinstance(result, Exception):
                    logger.error("dag_executor_task_exception",
                                 task_id=tid, error=str(result))
                    task_results[tid] = {"success": False, "error": str(result)}
                    # Mark as failed in TaskStore
                    if task_store:
                        await task_store.complete_task(
                            session_id, dag_id, tid, f"Error: {str(result)[:200]}")
                else:
                    task_results[tid] = result
                    logger.info("dag_executor_task_done",
                                 task_id=tid,
                                 success=result.get("success", False))
                    # Mark as completed in TaskStore (P1-1)
                    if task_store:
                        summary = _extract_result_summary(result)
                        unblocked = await task_store.complete_task(
                            session_id, dag_id, tid, summary)
                        if unblocked:
                            logger.info("dag_executor_tasks_unblocked",
                                        completed=tid, unblocked=unblocked)

    # Merge results into state-compatible format
    return _merge_final_results(task_results, state)


def _merge_final_results(task_results: dict[str, dict], state: dict) -> dict:
    """Merge all task results into a format compatible with the existing state."""
    merged = {
        "task_results": task_results,
        "final_response": "",
        "search_results": [],
        "recommendations": [],
        "response_type": "recommendation_cards",
        "response_data": {},
        "selected_product_ids": [],
        "stream_narrative": False,
    }

    all_search_results = []
    all_recommendations = []
    agent_responses = []

    for tid, result in task_results.items():
        if not result.get("success", False):
            continue

        data = result.get("data", {})
        if isinstance(data, dict):
            # Tool results: extract product data (supports both "data" and "results" keys)
            products = data.get("data") or data.get("results")
            if isinstance(products, list):
                for p in products:
                    pid = p.get("product_id", "")
                    if pid and pid not in {r.get("product_id") for r in all_search_results}:
                        all_search_results.append(p)

            # Agent results: extract response and recommendations
            if "final_response" in data:
                agent_responses.append(data["final_response"])
            if "recommendations" in data:
                for rec in data["recommendations"]:
                    pid = rec.get("product_id", "")
                    if pid and pid not in {r.get("product_id") for r in all_recommendations}:
                        all_recommendations.append(rec)
            if "response_type" in data:
                merged["response_type"] = data["response_type"]
            if "response_data" in data:
                merged["response_data"].update(data["response_data"])
            if "selected_product_ids" in data:
                merged["selected_product_ids"].extend(data["selected_product_ids"])
            if data.get("stream_narrative"):
                merged["stream_narrative"] = True

    merged["search_results"] = all_search_results
    merged["recommendations"] = all_recommendations

    # If agent used selection-only mode, extract product data from tool_calls_log
    if merged["selected_product_ids"] and not merged["search_results"]:
        selected_ids = set(merged["selected_product_ids"])
        for tid, result in task_results.items():
            if not result.get("success", False):
                continue
            data = result.get("data", {})
            if not isinstance(data, dict):
                continue
            # Check tool_calls_log for product_search results
            for entry in data.get("tool_calls_log", []):
                tool_result = entry.get("result", {})
                if isinstance(tool_result, dict):
                    inner_data = tool_result.get("data", {})
                    if isinstance(inner_data, dict):
                        products = inner_data.get("results") or inner_data.get("data")
                        if isinstance(products, list):
                            for p in products:
                                pid = p.get("product_id", "")
                                if pid and pid in selected_ids:
                                    merged["search_results"].append(p)

    # Use the last agent response as final_response, or combine
    if agent_responses:
        merged["final_response"] = agent_responses[-1]
    elif not merged["final_response"]:
        merged["final_response"] = "已完成所有任务。"

    return merged
