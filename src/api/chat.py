"""SSE Chat API — streaming endpoints for frontend consumption.

Endpoints:
- POST /api/chat: Send message, receive SSE stream
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

    Request body: {"message": str, "session_id": str?}
    Response: SSE stream
    """
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", str(uuid.uuid4()))

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
            async for event in run_shopping_stream(message, session_id=session_id):
                etype = event.get("event", "unknown")
                data = event.get("data", {})
                yield _sse_event(etype, data)
        except Exception as e:
            yield _sse_event("error", {"error": str(e), "severity": "high"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/chat/resume")
async def chat_resume(request: Request):
    """Resume after HITL interrupt.

    Request body: {"session_id": str, "confirmed": bool, "data": dict?}
    Response: SSE stream continuing from checkpoint
    """
    body = await request.json()
    session_id = body.get("session_id", "")
    confirmed = body.get("confirmed", False)

    if not confirmed:
        async def cancel_stream():
            yield _sse_event("explanation", {"text": "订单已取消"})
            yield _sse_event("done", {"latency_ms": 0})
        return StreamingResponse(cancel_stream(), media_type="text/event-stream")

    # For confirmed orders, simulate order placement
    from src.skills.order_skill import create_order, confirm_order

    async def resume_stream():
        # In a real implementation, this would resume from the LangGraph checkpoint
        # For MVP, we directly place the order
        order_data = body.get("data", {})
        order = create_order(
            product_id=order_data.get("product_id", ""),
            product_name=order_data.get("product_name", "商品"),
            quantity=order_data.get("quantity", 1),
            unit_price=order_data.get("unit_price", 0),
            final_price=order_data.get("final_price", 0),
        )
        confirmed_order = confirm_order(order["order_id"])
        yield _sse_event("explanation", {"text": f"下单成功！订单号：{confirmed_order['order_id']}"})
        yield _sse_event("done", {"latency_ms": 0})

    return StreamingResponse(resume_stream(), media_type="text/event-stream")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
