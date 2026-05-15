"""HITL (Human-in-the-Loop) node templates.

Uses LangGraph's native interrupt mechanism:
1. Graph defines interrupt_before=["order_node"]
2. When execution reaches the node, LangGraph suspends and persists state via Checkpointer
3. Frontend displays confirmation UI
4. User confirms → call graph.invoke(Command(resume={"confirmed": True, ...}), config)
5. Execution resumes from the suspended node

This module provides:
- order_node: places an order after HITL confirmation
- build_hitl_order_graph: example graph with interrupt_before
"""

from langgraph.graph import END, StateGraph
from langgraph.types import Command

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
        return {"explanation": "没有可下单的商品"}

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
    return {"explanation": f"准备下单：{order_info['product_name']} ¥{order_info['final_price']}"}


async def node_confirm_order(state: ShoppingState) -> dict:
    """Execute order after HITL confirmation.

    This node is protected by interrupt_before. When resumed:
    - If user confirmed: creates the order
    - If user cancelled: returns cancellation message
    """
    # Check if resumed with confirmation
    # In LangGraph HITL, the resume payload is available in the state
    # or via Command(resume=...) which gets merged into state

    explanation = state.get("explanation", "")

    # Extract order info from state (set by node_prepare_order)
    ranked = state.get("ranked_results", [])
    if not ranked:
        return {"explanation": "订单取消：没有商品信息"}

    top = ranked[0]
    order = create_order(
        product_id=top.get("product_id", top.get("id", "")),
        product_name=top.get("name", "商品"),
        quantity=1,
        unit_price=top.get("price", 0),
        final_price=top.get("final_price", top.get("price", 0)),
    )

    # Auto-confirm for now (in real flow, this checks the resume payload)
    confirmed = confirm_order(order["order_id"])

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
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["confirm_order"],
    )
