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
import uuid
from typing import AsyncGenerator

from langgraph.graph import END, StateGraph

from src.graph.agent_state import AgentState
from src.graph.checkpointer import get_checkpointer
from src.graph.fallback import node_fallback
from src.graph.postprocessing import node_postprocess
from src.graph.preprocessing import node_preprocess
from src.graph.react_node import node_react_loop, should_continue
from src.observability.logger import get_logger, generate_request_id, set_request_context

logger = get_logger("shopping_agent")


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
    set_request_context(request_id=request_id, session_id=user_id)

    graph = build_shopping_agent_graph()
    tid = thread_id or f"agent_{user_id}_{uuid.uuid4().hex[:8]}"

    # Build initial state
    initial_messages = messages or []
    initial_messages.append({"role": "user", "content": user_input})

    initial_state = {
        "messages": initial_messages,
        "user_id": user_id,
        "intent": "",
        "entities": {},
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
        search_results = state_values.get("search_results", [])

        # Send results event
        if search_results:
            top_results = search_results[:5]
            yield {"event": "results", "data": {"results": top_results}}

        # Send explanation event
        if final_response:
            yield {"event": "explanation", "data": {"text": final_response}}

        yield {"event": "done", "data": {"request_id": request_id}}

    except Exception as e:
        logger.error("agent_stream_error", error_type=type(e).__name__, error_message=str(e))
        yield {"event": "error", "data": {"error": str(e)}}
        yield {"event": "done", "data": {"request_id": request_id}}
