"""SSE Chat API — streaming endpoints for frontend consumption.

Endpoints:
- POST /api/chat: Send message, receive SSE stream
- POST /api/chat/order: Start order flow for a product (HITL)
- POST /api/chat/resume: Resume after HITL interrupt

SSE Events:
- intent: intent classification result
- entities: extracted entities
- clarification: clarification question
- results: ranked product results
- explanation: final recommendation explanation
- interrupt: HITL confirmation needed
- done: stream complete with latency
- error: error occurred
"""

import json
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from src.graph.hitl_nodes import build_hitl_order_graph
from src.graph.shopping_agent import run_agent_stream
from src.graph.shopping_graph import run_shopping_stream
from src.security.input_guard import InputViolation, validate_input

app = FastAPI(title="ShoppingAgent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sse_event(event: str, data: dict) -> str:
    """Format a server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message and receive SSE stream of shopping results.

    Request body: {"message": str, "session_id": str?, "mode": "agent"|"workflow"?}
    mode defaults to "agent" (hybrid Agent graph).
    mode="workflow" uses the original shopping_graph pipeline.
    """
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", str(uuid.uuid4()))
    mode = body.get("mode", "agent")

    # Input validation
    try:
        validate_input(message)
    except InputViolation as e:
        error_reason = e.reason
        error_severity = e.severity
        async def error_stream():
            yield _sse_event("error", {"error": error_reason, "severity": error_severity})
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    async def event_stream():
        try:
            if mode == "workflow":
                async for event in run_shopping_stream(message, session_id=session_id):
                    etype = event.get("event", "unknown")
                    data = event.get("data", {})
                    yield _sse_event(etype, data)
            else:
                async for event in run_agent_stream(message, user_id=session_id, thread_id=f"agent-{session_id}"):
                    etype = event.get("event", "unknown")
                    data = event.get("data", {})
                    yield _sse_event(etype, data)
        except Exception as e:
            yield _sse_event("error", {"error": str(e), "severity": "high"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/chat/order")
async def chat_order(request: Request):
    """Start order flow for a product (HITL interrupt).

    Request body: {"session_id": str, "product": {product_id, name, price, ...}}
    Response: SSE stream with interrupt event for confirmation
    """
    body = await request.json()
    session_id = body.get("session_id", str(uuid.uuid4()))
    product = body.get("product", {})

    # Build initial state with the product as ranked_results
    initial_state = {
        "messages": [],
        "tool_calls": [],
        "errors": [],
        "intent": "order",
        "entities": {},
        "memory_chunks": [],
        "search_results": [],
        "promotion_info": {},
        "ranked_results": [{
            "product_id": product.get("product_id", ""),
            "name": product.get("name", "商品"),
            "price": product.get("price", 0),
            "final_price": product.get("final_price", product.get("price", 0)),
        }],
        "explanation": "",
        "clarification_count": 0,
        "order_info": None,
        "resume_confirmed": None,
    }

    async def order_stream():
        order_app = build_hitl_order_graph()
        config = {"configurable": {"thread_id": f"order-{session_id}"}}

        order_info = None
        async for event in order_app.astream(initial_state, config=config):
            for node_name, node_output in event.items():
                if node_name == "prepare_order" and isinstance(node_output, dict):
                    order_info = node_output.get("order_info")

        # Graph paused at interrupt() inside confirm_order
        yield _sse_event("interrupt", order_info or product)
        yield _sse_event("done", {"latency_ms": 0})

    return StreamingResponse(order_stream(), media_type="text/event-stream")


@app.post("/api/chat/resume")
async def chat_resume(request: Request):
    """Resume after HITL interrupt.

    Request body: {"session_id": str, "confirmed": bool}
    Response: SSE stream continuing from checkpoint
    """
    body = await request.json()
    session_id = body.get("session_id", "")
    confirmed = body.get("confirmed", False)

    async def resume_stream():
        order_app = build_hitl_order_graph()
        config = {"configurable": {"thread_id": f"order-{session_id}"}}

        # Check if there's a pending interrupt checkpoint
        state = await order_app.aget_state(config)
        if state.next:
            # Resume from LangGraph checkpoint
            try:
                async for event in order_app.astream(
                    Command(resume=confirmed), config=config,
                ):
                    for node_name, node_output in event.items():
                        if isinstance(node_output, dict) and "explanation" in node_output:
                            yield _sse_event("explanation", {"text": node_output["explanation"]})
            except Exception as e:
                yield _sse_event("error", {"error": str(e), "severity": "high"})
        else:
            yield _sse_event("explanation", {"text": "没有待处理的订单"})

        yield _sse_event("done", {"latency_ms": 0})

    return StreamingResponse(resume_stream(), media_type="text/event-stream")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
