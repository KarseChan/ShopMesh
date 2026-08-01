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
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from src.graph.hitl_nodes import build_hitl_order_graph
from src.graph.multi_agent_graph import run_multi_agent_stream
from src.memory.conversation_store import get_conversations, get_messages
from src.security.input_guard import InputViolation, validate_input

from src.auth.router import router as auth_router
from src.auth.apikey_router import router as apikey_router
from src.auth.middleware import TenantMiddleware
from src.ratelimit.limiter import RateLimiter
from src.ratelimit.middleware import RateLimitMiddleware

# ---------- Rate limiter (lifecycle managed via lifespan) ----------

rate_limiter = RateLimiter()


@asynccontextmanager
async def lifespan(application: FastAPI):
    yield
    await rate_limiter.close()


app = FastAPI(title="ShoppingAgent API", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(apikey_router)

# Middleware execution order (LIFO): CORSMiddleware → TenantMiddleware → RateLimitMiddleware
# TenantMiddleware sets context vars, RateLimitMiddleware reads them
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
app.add_middleware(TenantMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sse_event(event: str, data: dict) -> str:
    """Format a server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/session/ensure")
async def ensure_session_endpoint(request: Request):
    """Ensure session is valid. Returns new session_id if the old one expired.

    Request body: {"session_id": str, "user_id": str}
    Response: {"session_id": str, "is_new": bool, "prev_session_id": str|null}
    """
    import asyncio
    body = await request.json()
    session_id = body.get("session_id", "")
    user_id = body.get("user_id", session_id)
    from src.memory.session_manager import ensure_session_sync
    result = await asyncio.to_thread(ensure_session_sync, session_id, user_id)
    return result


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message and receive SSE stream of shopping results.

    Request body: {"message": str, "session_id": str?}
    单一主路径:确定性意图路由 → 每意图一个 ReAct agent。
    """
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", str(uuid.uuid4()))
    user_id = body.get("user_id", session_id)  # fallback for backward compat
    is_new_session = body.get("is_new_session", False)

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
            # 单一主路径：确定性意图路由 → 每意图一个 ReAct agent。
            async for event in run_multi_agent_stream(message, user_id=user_id, session_id=session_id, thread_id=f"multi-{session_id}", is_new_session=is_new_session):
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
    user_id = body.get("user_id", session_id)
    product = body.get("product", {})

    # Build initial state with the product as ranked_results
    initial_state = {
        "messages": [],
        "tool_calls": [],
        "errors": [],
        "user_id": user_id,
        "session_id": session_id,
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
    user_id = body.get("user_id", session_id)
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


@app.post("/api/behavior")
async def report_behavior(request: Request):
    """Report a user behavior signal (click/select/reject/dwell).

    Request body: {
        "session_id": str,
        "action": "click" | "select" | "reject" | "dwell",
        "category": str?,
        "product_price": float?,
        "product_brand": str?,
        "product_id": str?,
        "duration_ms": int?
    }
    """
    body = await request.json()
    from src.tasks.memory_tasks import process_behavior_signal
    process_behavior_signal.delay(
        user_id=body.get("user_id", body.get("session_id", "default_user")),
        category=body.get("category", ""),
        action=body["action"],
        product_price=body.get("product_price"),
        product_brand=body.get("product_brand"),
        product_id=body.get("product_id"),
        duration_ms=body.get("duration_ms"),
    )
    return {"status": "ok"}


@app.get("/api/conversations")
async def list_conversations(user_id: str, limit: int = 20):
    """List conversations for a user, ordered by most recent activity."""
    import asyncio
    conversations = await asyncio.to_thread(get_conversations, user_id, limit)
    return {"conversations": conversations}


@app.get("/api/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, user_id: str, limit: int = 50):
    """Get messages for a conversation."""
    import asyncio
    messages = await asyncio.to_thread(get_messages, user_id, conversation_id, limit)
    return {"messages": messages}


# ── 购物车 直连 REST(前端点击直接调,不经 agent/LLM)──

def _cart_uid(body: dict) -> str:
    from src.auth.context import get_context_user_id
    return get_context_user_id() or body.get("user_id", "")


@app.get("/api/cart")
async def cart_get(user_id: str = ""):
    from src.auth.context import get_context_user_id
    from src.skills import cart_store
    uid = get_context_user_id() or user_id
    if not uid:
        return {"items": [], "count": 0, "total": 0}
    return await cart_store.get_cart(uid)


@app.post("/api/cart/add")
async def cart_add(request: Request):
    from src.skills import cart_store, order_service
    body = await request.json()
    uid = _cart_uid(body)
    if not uid:
        return {"ok": False, "message": "请先登录"}
    p = order_service._product(body.get("product_id", ""))
    if not p:
        return {"ok": False, "message": "商品不存在"}
    price = p.get("final_price") or p.get("price", 0)
    await cart_store.add_item(uid, p["product_id"], p.get("name", "商品"), price, int(body.get("quantity", 1)))
    return {"ok": True, **(await cart_store.get_cart(uid))}


@app.post("/api/cart/update")
async def cart_update(request: Request):
    from src.skills import cart_store
    body = await request.json()
    uid = _cart_uid(body)
    if not uid:
        return {"ok": False, "message": "请先登录"}
    await cart_store.set_qty(uid, body.get("product_id", ""), int(body.get("quantity", 0)))
    return {"ok": True, **(await cart_store.get_cart(uid))}


@app.post("/api/cart/remove")
async def cart_remove(request: Request):
    from src.skills import cart_store
    body = await request.json()
    uid = _cart_uid(body)
    if not uid:
        return {"ok": False, "message": "请先登录"}
    await cart_store.remove_item(uid, body.get("product_id", ""))
    return {"ok": True, **(await cart_store.get_cart(uid))}


# ── 购物:结算预览 → 确认下单 → 订单查询(购物功能 P2)──
# 人工闸门:下单必须显式 confirmed=true(SENSITIVE),对应设计里的 HITL 确认。

@app.post("/api/orders/preview")
async def order_preview(request: Request):
    """结算预览:从购物车按服务端实时价重算,检查库存。不落库、不扣减。"""
    from src.auth.context import get_context_user_id
    from src.skills import cart_store, order_service
    body = await request.json()
    user_id = get_context_user_id() or body.get("user_id", "")
    if not user_id:
        return {"ok": False, "message": "请先登录"}
    cart = await cart_store.get_cart(user_id)
    if not cart["items"]:
        return {"ok": False, "message": "购物车为空"}
    lines, total = [], 0.0
    for ci in cart["items"]:
        pid = ci["product_id"]
        stock = await order_service.get_stock(pid)
        price = ci["price"]
        qty = int(ci["qty"])
        total += price * qty
        lines.append({"product_id": pid, "name": ci.get("name"), "price": price,
                      "qty": qty, "in_stock": stock >= qty})
    return {"ok": True, "items": lines, "total_price": round(total, 2),
            "all_in_stock": all(x["in_stock"] for x in lines)}


@app.post("/api/orders")
async def create_order_endpoint(request: Request):
    """确认下单(须 confirmed=true)。确定性提交:重算价 + 原子库存 + 幂等 + 落库。"""
    from src.auth.context import get_context_user_id
    from src.skills import order_service
    body = await request.json()
    if not body.get("confirmed"):
        return {"ok": False, "message": "订单需用户确认（confirmed=true）"}  # 人工闸门
    user_id = get_context_user_id() or body.get("user_id", "")
    session_id = body.get("session_id", user_id)
    if not user_id:
        return {"ok": False, "message": "请先登录"}
    idem = body.get("idempotency_key")
    return await order_service.create_order_from_cart(user_id, session_id, idempotency_key=idem)


@app.get("/api/orders/{order_id}")
async def get_order_endpoint(order_id: str):
    """查询订单状态。"""
    from src.skills import order_service
    o = await order_service.get_order(order_id)
    return o or {"ok": False, "message": "订单不存在"}


# ── 支付(P3,沙箱)——只信验签 webhook,后端不接触支付凭证 ──

@app.post("/api/orders/{order_id}/pay")
async def order_pay(order_id: str):
    """创建支付会话,返回支付跳转地址(真实场景为支付网关 hosted checkout)。"""
    from src.skills import order_service, payment_service
    o = await order_service.get_order(order_id)
    if not o:
        return {"ok": False, "message": "订单不存在"}
    if o["status"] != "awaiting_payment":
        return {"ok": False, "message": f"订单状态({o['status']})不可支付"}
    return await payment_service.create_session(order_id, o["total_price"])


@app.post("/api/payments/webhook")
async def payment_webhook(request: Request):
    """支付方回调:验签后幂等地标记订单 paid。订单转 paid 只能经此路径。"""
    from src.skills import payment_service
    raw = await request.body()
    signature = request.headers.get("x-signature", "")
    return await payment_service.handle_webhook(raw, signature)


@app.post("/api/payments/{payment_ref}/simulate")
async def payment_simulate(payment_ref: str):
    """【mock 支付方】模拟用户完成付款 → 生成已签名 webhook 走验签路径。仅沙箱演示用。"""
    from src.skills import payment_service
    return await payment_service.simulate_payment(payment_ref)


@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Query Celery task status.

    Returns: {"task_id", "status", "result", "error"}
    status: PENDING / STARTED / SUCCESS / FAILURE / RETRY
    """
    from src.tasks.celery_app import celery_app
    result = celery_app.AsyncResult(task_id)
    response = {
        "task_id": task_id,
        "status": result.status,
    }
    if result.ready():
        if result.successful():
            response["result"] = result.result
        else:
            response["error"] = str(result.result)
    return response


@app.get("/api/admin/costs")
async def get_llm_costs(tenant_id: str | None = None, days: int = 7):
    """Query LLM usage costs.

    Query params:
        tenant_id: Filter by tenant (optional, defaults to all)
        days: Number of days to look back (default 7)

    Returns aggregated daily costs from in-memory accumulator and DB history.
    """
    from src.observability.cost_tracker import get_daily_costs, get_costs_from_db
    from datetime import datetime, timedelta

    # In-memory: today's costs
    daily = get_daily_costs(tenant_id)

    # DB: historical costs
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    history = []
    if tenant_id:
        history = get_costs_from_db(tenant_id, start_date, end_date)

    return {
        "today": daily,
        "history": history,
        "pricing": {
            "input_per_1m_tokens": _get_pricing("input"),
            "output_per_1m_tokens": _get_pricing("output"),
        },
    }


def _get_pricing(direction: str) -> float:
    """Get configured pricing for display."""
    from src.config import config as cfg
    pricing = cfg.get("cost_tracking", {}).get("pricing", {})
    if direction == "input":
        return pricing.get("input_per_1m", 0.15)
    return pricing.get("output_per_1m", 0.60)
