"""T3.4 Order Skill + HITL tests."""

import pytest
from unittest.mock import patch

from src.skills.order_skill import (
    create_order, get_order, confirm_order, cancel_order,
    clear_orders, SKILL_DEFINITION,
)
from src.graph.hitl_nodes import (
    node_prepare_order, node_confirm_order, build_hitl_order_graph,
)


@pytest.fixture(autouse=True)
def clean_orders():
    clear_orders()
    yield
    clear_orders()


# === Skill Definition ===

def test_order_skill_definition():
    assert SKILL_DEFINITION.name == "place_order"
    assert SKILL_DEFINITION.permissions == "write"
    assert "product_id" in SKILL_DEFINITION.parameters["properties"]


# === Create Order ===

def test_create_order_basic():
    order = create_order("p1", "奶茶", 1, 15, 15)
    assert order["order_id"].startswith("ORD-")
    assert order["status"] == "pending"
    assert order["product_name"] == "奶茶"
    assert order["final_price"] == 15


def test_create_order_with_address():
    order = create_order("p1", "奶茶", 2, 15, 28, address="北京市朝阳区")
    assert order["address"] == "北京市朝阳区"
    assert order["quantity"] == 2


def test_create_order_idempotent():
    order1 = create_order("p1", "奶茶", 1, 15, 15, request_id="req_001")
    order2 = create_order("p1", "奶茶", 1, 15, 15, request_id="req_001")
    assert order1["order_id"] == order2["order_id"]


def test_create_order_different_request_ids():
    order1 = create_order("p1", "奶茶", 1, 15, 15, request_id="req_001")
    order2 = create_order("p1", "奶茶", 1, 15, 15, request_id="req_002")
    assert order1["order_id"] != order2["order_id"]


# === Get Order ===

def test_get_order():
    order = create_order("p1", "奶茶", 1, 15, 15)
    got = get_order(order["order_id"])
    assert got is not None
    assert got["product_name"] == "奶茶"


def test_get_order_nonexistent():
    assert get_order("ORD-NONEXISTENT") is None


# === Confirm Order ===

def test_confirm_order():
    order = create_order("p1", "奶茶", 1, 15, 15)
    confirmed = confirm_order(order["order_id"])
    assert confirmed["status"] == "confirmed"
    assert "confirmed_at" in confirmed


def test_confirm_order_nonexistent():
    result = confirm_order("ORD-NONEXISTENT")
    assert "error" in result


def test_confirm_order_already_confirmed():
    order = create_order("p1", "奶茶", 1, 15, 15)
    confirm_order(order["order_id"])
    result = confirm_order(order["order_id"])
    assert "error" in result


# === Cancel Order ===

def test_cancel_order():
    order = create_order("p1", "奶茶", 1, 15, 15)
    cancelled = cancel_order(order["order_id"])
    assert cancelled["status"] == "cancelled"


def test_cancel_order_nonexistent():
    result = cancel_order("ORD-NONEXISTENT")
    assert "error" in result


def test_cancel_confirmed_order():
    order = create_order("p1", "奶茶", 1, 15, 15)
    confirm_order(order["order_id"])
    cancelled = cancel_order(order["order_id"])
    assert cancelled["status"] == "cancelled"


# === HITL Nodes ===

@pytest.mark.asyncio
async def test_node_prepare_order():
    state = {
        "ranked_results": [
            {"product_id": "p1", "name": "奶茶", "price": 15, "final_price": 15},
        ],
        "explanation": "",
    }
    result = await node_prepare_order(state)
    assert "准备下单" in result["explanation"]
    assert "奶茶" in result["explanation"]


@pytest.mark.asyncio
async def test_node_prepare_order_empty():
    state = {"ranked_results": [], "explanation": ""}
    result = await node_prepare_order(state)
    assert "没有" in result["explanation"]


@pytest.mark.asyncio
async def test_node_confirm_order():
    state = {
        "order_info": {"product_id": "p1", "product_name": "奶茶", "quantity": 1, "unit_price": 15, "final_price": 15},
        "explanation": "",
    }
    with patch("src.graph.hitl_nodes.interrupt", return_value=True):
        result = await node_confirm_order(state)
    assert "下单成功" in result["explanation"]
    assert "ORD-" in result["explanation"]


@pytest.mark.asyncio
async def test_node_confirm_order_cancelled():
    state = {
        "order_info": {"product_id": "p1", "product_name": "奶茶", "quantity": 1, "unit_price": 15, "final_price": 15},
        "explanation": "",
    }
    with patch("src.graph.hitl_nodes.interrupt", return_value=False):
        result = await node_confirm_order(state)
    assert "取消" in result["explanation"]


@pytest.mark.asyncio
async def test_node_confirm_order_empty():
    state = {"order_info": None, "explanation": ""}
    with patch("src.graph.hitl_nodes.interrupt", return_value=True):
        result = await node_confirm_order(state)
    assert "没有商品" in result["explanation"]


# === HITL Graph ===

def test_build_hitl_order_graph():
    graph = build_hitl_order_graph()
    assert graph is not None
    assert hasattr(graph, "ainvoke")
