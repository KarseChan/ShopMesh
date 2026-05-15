"""Order Skill — place orders with HITL confirmation and idempotency.

Write skill (PermissionLevel.WRITE): requires user confirmation before execution.
Uses LangGraph interrupt_before for Human-in-the-Loop flow.
"""

import uuid
from datetime import datetime, timezone

from src.observability.logger import get_logger
from src.skills.schema import PermissionLevel, SkillDefinition

logger = get_logger("order_skill")

SKILL_DEFINITION = SkillDefinition(
    name="place_order",
    description="下单购买商品，需要用户确认后执行",
    parameters={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "商品 ID"},
            "product_name": {"type": "string", "description": "商品名称"},
            "quantity": {"type": "integer", "description": "购买数量", "default": 1},
            "unit_price": {"type": "number", "description": "单价"},
            "final_price": {"type": "number", "description": "实付金额"},
            "address": {"type": "string", "description": "收货地址"},
        },
        "required": ["product_id", "product_name", "quantity", "unit_price", "final_price"],
    },
    permissions=PermissionLevel.WRITE,
    version="1.0.0",
)

# In-memory order store (idempotency key -> order)
_orders: dict[str, dict] = {}

# Idempotency: request_id -> order_id
_idempotency_map: dict[str, str] = {}

ORDER_TIMEOUT_SECONDS = 300  # 5 minutes


def create_order(
    product_id: str,
    product_name: str,
    quantity: int,
    unit_price: float,
    final_price: float,
    address: str = "",
    request_id: str | None = None,
) -> dict:
    """Create an order with idempotency guarantee.

    Args:
        product_id: Product identifier
        product_name: Product display name
        quantity: Number of items
        unit_price: Price per item
        final_price: Total after promotions
        address: Shipping address
        request_id: Idempotency key (same key = same order)

    Returns:
        {"order_id": str, "status": str, ...} order details
    """
    # Idempotency check
    if request_id and request_id in _idempotency_map:
        existing_id = _idempotency_map[request_id]
        if existing_id in _orders:
            logger.info("order_idempotent_hit", request_id=request_id, order_id=existing_id)
            return _orders[existing_id]

    order_id = f"ORD-{uuid.uuid4().hex[:12].upper()}"
    now = datetime.now(timezone.utc)

    order = {
        "order_id": order_id,
        "product_id": product_id,
        "product_name": product_name,
        "quantity": quantity,
        "unit_price": unit_price,
        "final_price": final_price,
        "address": address,
        "status": "pending",
        "created_at": now.isoformat(),
        "request_id": request_id,
    }

    _orders[order_id] = order
    if request_id:
        _idempotency_map[request_id] = order_id

    logger.info("order_created", order_id=order_id, product=product_name,
                quantity=quantity, final_price=final_price)
    return order


def get_order(order_id: str) -> dict | None:
    """Get order by ID."""
    return _orders.get(order_id)


def confirm_order(order_id: str) -> dict:
    """Confirm a pending order (called after HITL confirmation).

    Returns updated order or error.
    """
    order = _orders.get(order_id)
    if not order:
        return {"error": f"订单 {order_id} 不存在"}

    if order["status"] != "pending":
        return {"error": f"订单 {order_id} 状态为 {order['status']}，无法确认"}

    order["status"] = "confirmed"
    order["confirmed_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("order_confirmed", order_id=order_id)
    return order


def cancel_order(order_id: str) -> dict:
    """Cancel an order."""
    order = _orders.get(order_id)
    if not order:
        return {"error": f"订单 {order_id} 不存在"}

    if order["status"] not in ("pending", "confirmed"):
        return {"error": f"订单 {order_id} 状态为 {order['status']}，无法取消"}

    order["status"] = "cancelled"
    order["cancelled_at"] = datetime.now(timezone.utc).isoformat()

    logger.info("order_cancelled", order_id=order_id)
    return order


def clear_orders() -> None:
    """Clear all orders (for testing)."""
    _orders.clear()
    _idempotency_map.clear()
