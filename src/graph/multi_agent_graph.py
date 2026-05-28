"""Multi-Agent Graph — intent-specific specialized agents with shared preprocessing.

Flow (new Orchestrator DAG path):
    preprocess → orchestrator → dag_executor → postprocess → END

Flow (legacy path, preserved as fallback):
    preprocess → agent_router → search_recommend/detail_compare agent
        → (continue/end/fallback) → postprocess → END

Each agent has its own prompt, tool subset, and max_iterations.
The router is deterministic (if/else on user_goal), no LLM involved.

The original shopping_agent.py is preserved intact.
"""

import traceback
import uuid
from typing import AsyncGenerator

import httpx
from langgraph.graph import END, StateGraph

from src.graph.agent_state import AgentState
from src.graph.checkpointer import get_checkpointer
from src.graph.dag_executor import node_dag_executor
from src.graph.fallback import node_fallback
from src.graph.orchestrator import node_orchestrator
from src.graph.postprocessing import node_postprocess
from src.graph.preprocessing import node_preprocess
from src.graph.specialized_agents import (
    node_agent_router,
    node_detail_compare_agent,
    node_search_recommend_agent,
    route_to_agent,
    should_continue,
)
from src.graph.stream_utils import (
    extract_search_results_from_tool_log,
    stream_explanation,
    stream_narrative,
)
from src.observability.logger import get_logger, generate_request_id, set_request_context

logger = get_logger("multi_agent")


def _friendly_error(e: Exception) -> str:
    """Convert technical error to user-friendly message."""
    if isinstance(e, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return "抱歉，系统暂时无法连接到AI服务，请稍后再试。"
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500:
        return "抱歉，AI服务暂时不可用，请稍后再试。"
    return "抱歉，处理过程中出现了问题，请稍后再试。"


def build_multi_agent_graph(use_orchestrator: bool = True):
    """Build the multi-agent graph.

    When use_orchestrator=True (default), uses the new Orchestrator DAG path:
        preprocess → orchestrator → dag_executor → postprocess → END

    When use_orchestrator=False, uses the legacy path:
        preprocess → agent_router → search_recommend/detail_compare → should_continue → postprocess → END

    Both paths share preprocessing and postprocessing.
    """
    graph = StateGraph(AgentState)

    # Shared nodes
    graph.add_node("preprocess", node_preprocess)
    graph.add_node("postprocess", node_postprocess)

    if use_orchestrator:
        # === New Orchestrator DAG path ===
        graph.add_node("orchestrator", node_orchestrator)
        graph.add_node("dag_executor", node_dag_executor)

        # Entry: preprocess → orchestrator → dag_executor → postprocess → END
        graph.set_entry_point("preprocess")
        graph.add_edge("preprocess", "orchestrator")
        graph.add_edge("orchestrator", "dag_executor")
        graph.add_edge("dag_executor", "postprocess")
        graph.add_edge("postprocess", END)
    else:
        # === Legacy path (preserved as fallback) ===
        graph.add_node("agent_router", node_agent_router)
        graph.add_node("fallback", node_fallback)
        graph.add_node("search_recommend_agent", node_search_recommend_agent)
        graph.add_node("detail_compare_agent", node_detail_compare_agent)

        # Entry: preprocess → router
        graph.set_entry_point("preprocess")
        graph.add_edge("preprocess", "agent_router")

        # Router → agent (deterministic conditional)
        # __order__ is handled by agent_router directly (sets final_response),
        # so route_to_agent returns "end" for it — skip agent nodes entirely.
        graph.add_conditional_edges("agent_router", route_to_agent, {
            "search_recommend_agent": "search_recommend_agent",
            "detail_compare_agent": "detail_compare_agent",
            "__order__": "postprocess",
        })

        # Each agent → should_continue → self / postprocess / fallback
        for agent_node in ("search_recommend_agent", "detail_compare_agent"):
            graph.add_conditional_edges(agent_node, should_continue, {
                "continue": agent_node,
                "end": "postprocess",
                "fallback": "fallback",
            })

        # Fallback → postprocess
        graph.add_edge("fallback", "postprocess")

        # Postprocess → END
        graph.add_edge("postprocess", END)

    checkpointer = get_checkpointer("memory")
    return graph.compile(checkpointer=checkpointer)


async def run_multi_agent_stream(
    user_input: str,
    user_id: str = "default_user",
    session_id: str | None = None,
    thread_id: str | None = None,
    messages: list | None = None,
    mode: str = "orchestrator",
) -> AsyncGenerator[dict, None]:
    """Run the multi-agent graph and yield SSE events.

    Args:
        mode: "orchestrator" (default) for DAG-based execution,
              "legacy" for the old agent_router path.
    """
    request_id = generate_request_id()
    set_request_context(request_id=request_id, session_id=session_id or user_id)

    use_orchestrator = mode == "orchestrator"
    graph = build_multi_agent_graph(use_orchestrator=use_orchestrator)
    tid = thread_id or f"multi_{user_id}_{uuid.uuid4().hex[:8]}"

    initial_messages = messages or []
    initial_messages.append({"role": "user", "content": user_input})

    initial_state = {
        "messages": initial_messages,
        "user_id": user_id,
        "session_id": session_id or tid,
        "intent": {},
        "user_goals": [],
        # entities: 不覆盖，让 checkpointer 保留前一轮值，实现 follow-up 上下文继承
        "memory_chunks": [],
        "search_plan": {},
        "search_results": [],
        "user_profile": {},
        "tool_calls_log": [],
        "iteration": 0,
        "max_iterations": 5,
        "final_response": None,
        "asked_fields": [],
        "used_fallback": False,
        "active_agent": "",
        "response_type": "",
        "response_data": {},
        # Orchestrator DAG fields
        "task_dag": [],
        "task_results": {},
        "task_status": "pending",
    }

    config = {"configurable": {"thread_id": tid}}

    try:
        results_yielded = False
        yield {"event": "status", "data": {"phase": "thinking", "message": "正在分析您的需求..."}}

        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chain_end":
                node_name = event.get("name", "")
                output = event.get("data", {}).get("output", {})

                if not output or not isinstance(output, dict):
                    continue

                if node_name == "preprocess":
                    yield {"event": "intent", "data": {"intent": output.get("intent", "")}}
                    yield {"event": "entities", "data": {"entities": output.get("entities", {})}}

                elif node_name == "orchestrator":
                    task_dag = output.get("task_dag", [])
                    yield {"event": "status", "data": {
                        "phase": "planning",
                        "message": f"已规划 {len(task_dag)} 个任务",
                    }}
                    yield {"event": "task_dag", "data": {
                        "tasks": [t.get("task_id") for t in task_dag],
                        "dependencies": {t["task_id"]: t.get("depends_on", []) for t in task_dag},
                    }}

                elif node_name == "dag_executor":
                    task_results = output.get("task_results", {})
                    completed = sum(1 for r in task_results.values()
                                   if isinstance(r, dict) and r.get("success"))
                    yield {"event": "status", "data": {
                        "phase": "executing",
                        "message": f"已完成 {completed}/{len(task_results)} 个任务",
                    }}

                elif node_name in ("search_recommend_agent", "detail_compare_agent"):
                    tool_log = output.get("tool_calls_log", [])
                    if tool_log:
                        latest = tool_log[-1]
                        tool_name = latest.get("tool", "")
                        yield {"event": "tool_call", "data": {
                            "tool": tool_name,
                            "args": latest.get("args", {}),
                        }}
                        # Show progress to user
                        if tool_name in ("product_search", "multi_query_search"):
                            yield {"event": "status", "data": {"phase": "searching", "message": "正在搜索商品..."}}
                        elif tool_name == "ask_clarification":
                            yield {"event": "status", "data": {"phase": "clarifying", "message": "需要更多信息..."}}

                elif node_name == "fallback":
                    yield {"event": "fallback", "data": {"used": True}}

        # Get complete final state (has accumulated tool_calls_log across all iterations)
        final_state = await graph.aget_state(config)
        state_values = final_state.values if final_state else {}

        final_response = state_values.get("final_response", "")
        response_type = state_values.get("response_type", "recommendation_cards")
        response_data = state_values.get("response_data", {})

        # Extract search results from accumulated tool_calls_log
        search_results = state_values.get("search_results", [])
        tool_log = state_values.get("tool_calls_log", [])
        used_fallback = state_values.get("used_fallback", False)
        if not search_results and not used_fallback:
            search_results = extract_search_results_from_tool_log(tool_log)

        # Supplement from response_data
        if response_data:
            resp_products = response_data.get("products", [])
            if resp_products:
                existing_ids = {p.get("product_id") or p.get("id") for p in search_results}
                for rp in resp_products:
                    rpid = rp.get("product_id")
                    if rpid and rpid not in existing_ids:
                        search_results.append(rp)
                        existing_ids.add(rpid)

        recommendations = (
            state_values.get("recommendations", [])
            or response_data.get("recommendations", [])
            or response_data.get("products", [])
        )

        # Extract user query from messages
        user_query = ""
        for msg in reversed(state_values.get("messages", [])):
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "type", "")
            if role == "user":
                user_query = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
                break

        # Narrative streaming: real LLM streaming per product
        selected_product_ids = state_values.get("selected_product_ids", [])
        use_narrative = state_values.get("stream_narrative", False)

        if (use_narrative and selected_product_ids and search_results
                and response_type != "comparison_table"):
            yield {"event": "status", "data": {"phase": "preparing", "message": "正在为您整理推荐..."}}
            logger.info("narrative_stream_start",
                         product_count=len(search_results),
                         selected_count=len(selected_product_ids),
                         response_type=response_type)
            async for event in stream_narrative(
                selected_product_ids=selected_product_ids,
                search_results=search_results,
                user_query=user_query,
                agent_summary=final_response,
            ):
                yield event
        elif search_results and recommendations:
            # Fallback: old interleaved path (pre-generated text + cards)
            logger.info("sse_results_emit",
                         product_count=len(search_results),
                         response_type=response_type)
            yield {"event": "results", "data": {
                "products": search_results,
                "recommendations": recommendations,
                "response_type": response_type,
                "response_data": response_data,
            }}

            if final_response:
                async for token in stream_explanation(state_values):
                    yield {"event": "explanation_delta", "data": {"delta": token}}
                yield {"event": "explanation", "data": {"text": final_response}}
        else:
            # Non-recommendation: text only
            if final_response:
                async for token in stream_explanation(state_values):
                    yield {"event": "explanation_delta", "data": {"delta": token}}
                yield {"event": "explanation", "data": {"text": final_response}}

        yield {"event": "done", "data": {"request_id": request_id}}

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("multi_agent_stream_error", error_type=type(e).__name__, error_message=str(e), traceback=tb)
        user_msg = _friendly_error(e)
        yield {"event": "error", "data": {"error": user_msg, "severity": "low"}}
        yield {"event": "done", "data": {"request_id": request_id}}
