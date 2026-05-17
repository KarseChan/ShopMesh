"""HITL (Human-in-the-Loop) node templates.

Uses LangGraph's native interrupt mechanism:
1. Node calls interrupt() to pause execution and wait for user input
2. When resumed via Command(resume=value), interrupt() returns the value
3. Node uses the value to decide confirm/cancel

This module provides:
- node_prepare_order: prepares order details
- node_confirm_order: waits for HITL confirmation via interrupt()
- build_hitl_order_graph: order subgraph
"""

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from src.graph.checkpointer import get_checkpointer
from src.graph.state import ShoppingState
from src.observability.logger import get_logger
from src.skills.order_skill import confirm_order, create_order

logger = get_logger("hitl_nodes")


async def node_prepare_order(state: ShoppingState) -> dict:
    """Prepare order details from ranked results (before HITL interrupt).

    Extracts the top-ranked product and prepares order info.
    The graph will interrupt before node_confirm_order executes.
    """
    ranked = state.get("ranked_results", [])
    if not ranked:
        return {"explanation": "没有可下单的商品", "order_info": None}

    top = ranked[0]
    order_info = {
        "product_id": top.get("product_id", top.get("id", "")),
        "product_name": top.get("name", "商品"),
        "quantity": 1,
        "unit_price": top.get("price", 0),
        "final_price": top.get("final_price", top.get("price", 0)),
    }

    logger.info("order_prepared", product=order_info["product_name"],
                price=order_info["final_price"])
    return {
        "explanation": f"准备下单：{order_info['product_name']} ¥{order_info['final_price']}",
        "order_info": order_info,
    }


async def node_confirm_order(state: ShoppingState) -> dict:
    """Execute order after HITL confirmation.

    Calls interrupt() to pause execution and wait for user input.
    When resumed via Command(resume=True/False), interrupt() returns the value.
    """
    # Pause here and wait for user confirmation
    confirmed = interrupt("请确认下单")

    if not confirmed:
        logger.info("order_cancelled_by_user")
        return {"explanation": "订单已取消"}

    order_info = state.get("order_info")
    if not order_info:
        return {"explanation": "订单取消：没有商品信息"}

    order = create_order(
        product_id=order_info.get("product_id", ""),
        product_name=order_info.get("product_name", "商品"),
        quantity=order_info.get("quantity", 1),
        unit_price=order_info.get("unit_price", 0),
        final_price=order_info.get("final_price", 0),
    )
    confirm_order(order["order_id"])

    logger.info("order_placed", order_id=order["order_id"])
    return {"explanation": f"下单成功！订单号：{order['order_id']}"}


def build_hitl_order_graph():
    """Build a graph with HITL interrupt before order confirmation.

    Flow: prepare_order → [interrupt] → confirm_order → END

    Usage:
        app = build_hitl_order_graph()
        # First invoke: runs until interrupt
        result = await app.ainvoke(initial_state, config)
        # User confirms via frontend
        result = await app.ainvoke(Command(resume=True), config)
    """
    graph = StateGraph(ShoppingState)

    graph.add_node("prepare_order", node_prepare_order)
    graph.add_node("confirm_order", node_confirm_order)

    graph.set_entry_point("prepare_order")
    graph.add_edge("prepare_order", "confirm_order")
    graph.add_edge("confirm_order", END)

    checkpointer = get_checkpointer("memory")
    return graph.compile(checkpointer=checkpointer)
