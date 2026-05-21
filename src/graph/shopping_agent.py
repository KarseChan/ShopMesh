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
        # Stream graph execution
        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")

            # Node completion events
            if kind == "on_chain_end":
                node_name = event.get("name", "")
                output = event.get("data", {}).get("output", {})

                if node_name == "preprocess" and output:
                    yield {"event": "intent", "data": {"intent": output.get("intent", "")}}
                    yield {"event": "entities", "data": {"entities": output.get("entities", {})}}

                elif node_name == "react_loop" and output:
                    # Stream tool call info if available
                    tool_log = output.get("tool_calls_log", [])
                    if tool_log:
                        latest = tool_log[-1]
                        yield {"event": "tool_call", "data": {
                            "tool": latest.get("tool", ""),
                            "args": latest.get("args", {}),
                        }}

                elif node_name == "fallback" and output:
                    yield {"event": "fallback", "data": {"used": True}}

                elif node_name == "postprocess" and output:
                    pass  # No user-facing event needed

        # Get final state for the final response
        final_state = await graph.aget_state(config)
        state_values = final_state.values if final_state else {}

        final_response = state_values.get("final_response", "")

        # Extract product results from tool_calls_log (agent mode)
        # In agent graph, product_search results are in tool_calls_log, not search_results
        search_results = state_values.get("search_results", [])
        used_fallback = state_values.get("used_fallback", False)
        # Only extract from tool_calls_log when fallback was NOT used.
        # tool_calls_log accumulates across turns; when fallback fires (agent failed),
        # the log contains stale entries from previous turns.
        if not search_results and not used_fallback:
            for entry in reversed(state_values.get("tool_calls_log", [])):
                if entry.get("tool") in ("product_search", "multi_query_search"):
                    tool_data = entry.get("result", {})
                    if isinstance(tool_data, dict):
                        data = tool_data.get("data", tool_data)
                        search_results = data.get("results", [])
                    break

        # Get structured recommendations
        recommendations = state_values.get("recommendations", [])

        # Send results event only when there are actual recommendations
        if search_results and recommendations:
            top_results = search_results[:5]
            # Filter recommendations to only include products in top_results
            top_ids = {p.get("product_id", "") for p in top_results}
            top_recs = [r for r in recommendations if r.get("product_id", "") in top_ids] if recommendations else []
            yield {"event": "results", "data": {
                "products": top_results,
                "recommendations": top_recs,
            }}

        # Send explanation event
        if final_response:
            yield {"event": "explanation", "data": {"text": final_response}}

        yield {"event": "done", "data": {"request_id": request_id}}

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("agent_stream_error", error_type=type(e).__name__, error_message=str(e),
                     traceback=tb)
        user_msg = _friendly_error(e)
        yield {"event": "error", "data": {"error": user_msg, "severity": "low"}}
        yield {"event": "done", "data": {"request_id": request_id}}
