"""Multi-Agent Graph — intent-specific specialized agents with shared preprocessing.

Flow:
    preprocess → agent_router → recommend/search/detail/compare/order agent
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
from src.graph.fallback import node_fallback
from src.graph.postprocessing import node_postprocess
from src.graph.preprocessing import node_preprocess
from src.graph.specialized_agents import (
    node_agent_router,
    node_compare_agent,
    node_detail_agent,
    node_order_agent,
    node_recommend_agent,
    node_search_agent,
    route_to_agent,
    should_continue,
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


def build_multi_agent_graph():
    """Build the multi-agent graph.

    Nodes:
        preprocess     — deterministic: intent + entity + memory (parallel) + clarification routing
        agent_router   — deterministic: if/else on user_goal → agent name
        recommend_agent — ReAct loop: recommendation-specific prompt + tools
        search_agent   — ReAct loop: search-specific prompt + tools
        detail_agent   — ReAct loop: detail-specific prompt + tools
        compare_agent  — ReAct loop: compare-specific prompt + tools
        order_agent    — minimal: no loop, direct response
        fallback       — deterministic pipeline backup
        postprocess    — deterministic: preference extraction + memory write

    Edges:
        preprocess → agent_router
        agent_router → (recommend|search|detail|compare|order)_agent  [conditional]
        each agent → should_continue → "continue" (self) | "end" (postprocess) | "fallback"
        order_agent → postprocess  [direct, no loop]
        fallback → postprocess
        postprocess → END
    """
    graph = StateGraph(AgentState)

    # Shared nodes
    graph.add_node("preprocess", node_preprocess)
    graph.add_node("agent_router", node_agent_router)
    graph.add_node("fallback", node_fallback)
    graph.add_node("postprocess", node_postprocess)

    # Specialized agent nodes
    graph.add_node("recommend_agent", node_recommend_agent)
    graph.add_node("search_agent", node_search_agent)
    graph.add_node("detail_agent", node_detail_agent)
    graph.add_node("compare_agent", node_compare_agent)
    graph.add_node("order_agent", node_order_agent)

    # Entry: preprocess → router
    graph.set_entry_point("preprocess")
    graph.add_edge("preprocess", "agent_router")

    # Router → agent (deterministic conditional)
    graph.add_conditional_edges("agent_router", route_to_agent, {
        "recommend_agent": "recommend_agent",
        "search_agent": "search_agent",
        "detail_agent": "detail_agent",
        "compare_agent": "compare_agent",
        "order_agent": "order_agent",
    })

    # Each agent → should_continue → self / postprocess / fallback
    for agent_node in ("recommend_agent", "search_agent", "detail_agent", "compare_agent"):
        graph.add_conditional_edges(agent_node, should_continue, {
            "continue": agent_node,
            "end": "postprocess",
            "fallback": "fallback",
        })

    # Order agent → postprocess directly (no loop)
    graph.add_edge("order_agent", "postprocess")

    # Fallback → postprocess
    graph.add_edge("fallback", "postprocess")

    # Postprocess → END
    graph.add_edge("postprocess", END)

    checkpointer = get_checkpointer("memory")
    return graph.compile(checkpointer=checkpointer)


async def run_multi_agent_stream(
    user_input: str,
    user_id: str = "default_user",
    thread_id: str | None = None,
    messages: list | None = None,
) -> AsyncGenerator[dict, None]:
    """Run the multi-agent graph and yield SSE events.

    Same signature and event format as run_agent_stream() in shopping_agent.py.
    """
    request_id = generate_request_id()
    set_request_context(request_id=request_id, session_id=user_id)

    graph = build_multi_agent_graph()
    tid = thread_id or f"multi_{user_id}_{uuid.uuid4().hex[:8]}"

    initial_messages = messages or []
    initial_messages.append({"role": "user", "content": user_input})

    initial_state = {
        "messages": initial_messages,
        "user_id": user_id,
        "intent": {},
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
    }

    config = {"configurable": {"thread_id": tid}}

    try:
        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chain_end":
                node_name = event.get("name", "")
                output = event.get("data", {}).get("output", {})

                if node_name == "preprocess" and output:
                    yield {"event": "intent", "data": {"intent": output.get("intent", "")}}
                    yield {"event": "entities", "data": {"entities": output.get("entities", {})}}

                elif node_name in ("recommend_agent", "search_agent", "detail_agent", "compare_agent", "order_agent") and output:
                    tool_log = output.get("tool_calls_log", [])
                    if tool_log:
                        latest = tool_log[-1]
                        yield {"event": "tool_call", "data": {
                            "tool": latest.get("tool", ""),
                            "args": latest.get("args", {}),
                        }}

                elif node_name == "fallback" and output:
                    yield {"event": "fallback", "data": {"used": True}}

        # Get final state
        final_state = await graph.aget_state(config)
        state_values = final_state.values if final_state else {}

        final_response = state_values.get("final_response", "")
        response_type = state_values.get("response_type", "recommendation_cards")
        response_data = state_values.get("response_data", {})

        # Extract search results from tool_calls_log
        search_results = state_values.get("search_results", [])
        tool_log = state_values.get("tool_calls_log", [])
        if not search_results:
            seen_ids = set()
            for entry in tool_log:
                if entry.get("tool") in ("product_search", "multi_query_search"):
                    tool_data = entry.get("result", {})
                    if not isinstance(tool_data, dict):
                        continue
                    data = tool_data.get("data", tool_data)
                    if not isinstance(data, dict):
                        continue
                    results_list = data.get("results", [])
                    for r in results_list:
                        rid = r.get("product_id") or r.get("id")
                        if rid and rid not in seen_ids:
                            seen_ids.add(rid)
                            search_results.append(r)

        # Supplement from response_data: for compare/detail agents, response_data
        # contains the full product list the agent wants to display.
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

        # Results event: emit when there are actual search results to display.
        # For detail_card, search_results is the single product being detailed.
        # For product_grid / recommendation_cards, search_results are the matched products.
        # If agent only asked clarification (no search), search_results stays empty.
        if search_results:
            # Collect product_ids referenced in recommendations
            rec_ids = {r.get("product_id", "") for r in recommendations if r.get("product_id")}
            # Ensure all recommended products are in the products list
            result_ids = {p.get("product_id", "") for p in search_results}
            logger.info("sse_results_emit",
                         product_count=len(search_results),
                         product_ids=[p.get("product_id") for p in search_results],
                         rec_count=len(recommendations),
                         rec_ids=list(rec_ids),
                         response_type=response_type,
                         final_response_preview=final_response[:200] if final_response else "")
            yield {"event": "results", "data": {
                "products": search_results,
                "recommendations": recommendations,
                "response_type": response_type,
                "response_data": response_data,
            }}

        # Explanation event
        if final_response:
            yield {"event": "explanation", "data": {"text": final_response}}

        yield {"event": "done", "data": {"request_id": request_id}}

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("multi_agent_stream_error", error_type=type(e).__name__, error_message=str(e), traceback=tb)
        user_msg = _friendly_error(e)
        yield {"event": "error", "data": {"error": user_msg, "severity": "low"}}
        yield {"event": "done", "data": {"request_id": request_id}}
