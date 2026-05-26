"""Shopping Agent Graph — hybrid architecture: deterministic preprocessing + ReAct.

Flow:
    preprocess → react_loop → success → postprocess → END
                           ↓ failure
                        fallback → postprocess → END

The original shopping_graph.py is preserved intact.
This module provides the new Agent-based alternative.
"""

import asyncio
import json
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
from src.graph.react_node import node_react_loop, should_continue
from src.graph.stream_utils import (
    extract_search_results_from_tool_log,
    stream_explanation,
    stream_narrative,
)
from src.observability.logger import get_logger, generate_request_id, set_request_context

logger = get_logger("shopping_agent")


def _friendly_error(e: Exception) -> str:
    """Convert technical error to user-friendly message."""
    if isinstance(e, (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)):
        return "抱歉，系统暂时无法连接到AI服务，请稍后再试。"
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500:
        return "抱歉，AI服务暂时不可用，请稍后再试。"
    return "抱歉，处理过程中出现了问题，请稍后再试。"


def build_shopping_agent_graph():
    """Build the hybrid Agent graph.

    Nodes:
        preprocess — deterministic: intent + entity + memory (parallel)
        react_loop — dynamic: ReAct Thought → Action → Observation
        fallback   — stable pipeline backup
        postprocess — deterministic: preference extraction + memory write

    Edges:
        preprocess → react_loop
        react_loop → should_continue → "continue" | "end" | "fallback"
        fallback → postprocess
        postprocess → END
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("preprocess", node_preprocess)
    graph.add_node("react_loop", node_react_loop)
    graph.add_node("fallback", node_fallback)
    graph.add_node("postprocess", node_postprocess)

    # Fixed flow: preprocess → react_loop
    graph.set_entry_point("preprocess")
    graph.add_edge("preprocess", "react_loop")

    # Conditional: react_loop → continue / end / fallback
    graph.add_conditional_edges("react_loop", should_continue, {
        "continue": "react_loop",
        "end": "postprocess",
        "fallback": "fallback",
    })

    # Fallback → postprocess
    graph.add_edge("fallback", "postprocess")

    # Postprocess → END
    graph.add_edge("postprocess", END)

    checkpointer = get_checkpointer("memory")
    return graph.compile(checkpointer=checkpointer)


async def run_agent_stream(
    user_input: str,
    user_id: str = "default_user",
    session_id: str | None = None,
    thread_id: str | None = None,
    messages: list | None = None,
) -> AsyncGenerator[dict, None]:
    """Run the shopping agent and yield SSE events.

    Yields event dicts with "event" and "data" keys for SSE streaming.
    Compatible with existing SSE event format.

    Args:
        user_input: User's query text
        user_id: User identifier
        thread_id: Conversation thread ID (for checkpointer)
        messages: Prior dialog history

    Yields:
        {"event": str, "data": dict}
    """
    request_id = generate_request_id()
    set_request_context(request_id=request_id, session_id=session_id or user_id)

    graph = build_shopping_agent_graph()
    tid = thread_id or f"agent_{user_id}_{uuid.uuid4().hex[:8]}"

    # Build initial state
    initial_messages = messages or []
    initial_messages.append({"role": "user", "content": user_input})

    initial_state = {
        "messages": initial_messages,
        "user_id": user_id,
        "session_id": session_id or tid,
        "session_window": [],
        "session_summary": "",
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
    }

    config = {"configurable": {"thread_id": tid}}

    try:
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

                elif node_name == "react_loop":
                    tool_log = output.get("tool_calls_log", [])
                    if tool_log:
                        latest = tool_log[-1]
                        tool_name = latest.get("tool", "")
                        yield {"event": "tool_call", "data": {
                            "tool": tool_name,
                            "args": latest.get("args", {}),
                        }}
                        if tool_name in ("product_search", "multi_query_search"):
                            yield {"event": "status", "data": {"phase": "searching", "message": "正在搜索商品..."}}

                elif node_name == "fallback":
                    yield {"event": "fallback", "data": {"used": True}}

                elif node_name == "postprocess":
                    pass

        # Get complete final state (has accumulated tool_calls_log across all iterations)
        final_state = await graph.aget_state(config)
        state_values = final_state.values if final_state else {}

        final_response = state_values.get("final_response", "")

        # Extract search results from accumulated tool_calls_log
        search_results = state_values.get("search_results", [])
        tool_log = state_values.get("tool_calls_log", [])
        used_fallback = state_values.get("used_fallback", False)
        if not search_results and not used_fallback:
            search_results = extract_search_results_from_tool_log(tool_log)

        # Supplement from response_data
        response_data = state_values.get("response_data", {})
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
            or state_values.get("response_data", {}).get("recommendations", [])
            or state_values.get("response_data", {}).get("products", [])
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

        if (use_narrative and selected_product_ids and search_results):
            yield {"event": "status", "data": {"phase": "preparing", "message": "正在为您整理推荐..."}}
            logger.info("narrative_stream_start",
                         product_count=len(search_results),
                         selected_count=len(selected_product_ids))
            async for event in stream_narrative(
                selected_product_ids=selected_product_ids,
                search_results=search_results,
                user_query=user_query,
                agent_summary=final_response,
            ):
                yield event
        else:
            # Non-recommendation: old path (results + explanation)
            if search_results:
                yield {"event": "results", "data": {
                    "products": search_results[:5],
                    "recommendations": recommendations,
                }}

            if final_response:
                async for token in stream_explanation(state_values):
                    yield {"event": "explanation_delta", "data": {"delta": token}}
                yield {"event": "explanation", "data": {"text": final_response}}

        yield {"event": "done", "data": {"request_id": request_id}}

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("agent_stream_error", error_type=type(e).__name__, error_message=str(e),
                     traceback=tb)
        user_msg = _friendly_error(e)
        yield {"event": "error", "data": {"error": user_msg, "severity": "low"}}
        yield {"event": "done", "data": {"request_id": request_id}}
