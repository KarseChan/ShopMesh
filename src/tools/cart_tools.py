"""购物车 Agent 工具(P1)。

安全边界:
- user_id 一律从**认证上下文**(get_context_user_id)取,工具 schema 不含 user_id,
  LLM 无法指定 → 不能操作他人购物车。
- 加购/改量/删除为 WRITE(执行器要求 user_confirmed);查看为 READ。
"""

from src.auth.context import get_context_user_id
from src.observability.logger import get_logger
from src.skills import cart_store
from src.skills.schema import PermissionLevel
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("cart_tools")

_PRODUCT_MAP = None


def _product(product_id: str) -> dict | None:
    global _PRODUCT_MAP
    if _PRODUCT_MAP is None:
        from src.tools.search_tool import load_products
        _PRODUCT_MAP = {p["product_id"]: p for p in load_products() if p.get("product_id")}
    return _PRODUCT_MAP.get(product_id)


def _uid() -> str | None:
    uid = get_context_user_id()
    return uid or None


async def add_to_cart(product_id: str, quantity: int = 1) -> dict:
    """加入购物车。商品名/价格由服务端按 product_id 查出(不信 LLM)。"""
    uid = _uid()
    if not uid:
        return {"ok": False, "message": "请先登录后再使用购物车"}
    p = _product(product_id)
    if not p:
        return {"ok": False, "message": f"商品 {product_id} 不存在"}
    if int(quantity) == 0:
        quantity = 1
    price = p.get("final_price") or p.get("price", 0)
    item = await cart_store.add_item(uid, product_id, p.get("name", "商品"), price, int(quantity))
    cart = await cart_store.get_cart(uid)
    return {"ok": True, "message": f"已加入购物车：{item['name']} ×{item['qty']}",
            "item": item, "cart_count": cart["count"], "cart_total": cart["total"]}


async def view_cart() -> dict:
    """查看当前购物车。"""
    uid = _uid()
    if not uid:
        return {"ok": False, "message": "请先登录后再使用购物车"}
    cart = await cart_store.get_cart(uid)
    return {"ok": True, **cart}


async def update_cart(product_id: str, quantity: int) -> dict:
    """修改购物车中某商品数量(quantity<=0 视为移除)。"""
    uid = _uid()
    if not uid:
        return {"ok": False, "message": "请先登录后再使用购物车"}
    hit = await cart_store.set_qty(uid, product_id, int(quantity))
    if not hit and int(quantity) > 0:
        return {"ok": False, "message": "购物车中没有该商品"}
    cart = await cart_store.get_cart(uid)
    return {"ok": True, "message": "已更新购物车", **cart}


async def remove_from_cart(product_id: str) -> dict:
    """从购物车移除某商品。"""
    uid = _uid()
    if not uid:
        return {"ok": False, "message": "请先登录后再使用购物车"}
    removed = await cart_store.remove_item(uid, product_id)
    cart = await cart_store.get_cart(uid)
    return {"ok": True, "message": "已移除" if removed else "购物车中没有该商品", **cart}


# ── 注册 ──
tool_registry.register(ToolDef(
    name="add_to_cart",
    description="将商品加入购物车。用户明确表达'加购物车/我要买/收藏这个'等意图时调用。传入 product_id 和数量。",
    parameters={
        "type": "object",
        "properties": {
            "product_id": {"type": "string", "description": "商品 ID（来自检索结果）"},
            "quantity": {"type": "integer", "description": "数量，默认 1", "default": 1},
        },
        "required": ["product_id"],
    },
    return_type="dict",
    func=add_to_cart,
    permissions=PermissionLevel.WRITE,
))

tool_registry.register(ToolDef(
    name="view_cart",
    description="查看当前用户的购物车内容（商品、数量、合计）。",
    parameters={"type": "object", "properties": {}},
    return_type="dict",
    func=view_cart,
    permissions=PermissionLevel.READ,
))

tool_registry.register(ToolDef(
    name="update_cart",
    description="修改购物车中某商品的数量。quantity<=0 表示移除。",
    parameters={
        "type": "object",
        "properties": {
            "product_id": {"type": "string"},
            "quantity": {"type": "integer"},
        },
        "required": ["product_id", "quantity"],
    },
    return_type="dict",
    func=update_cart,
    permissions=PermissionLevel.WRITE,
))

tool_registry.register(ToolDef(
    name="remove_from_cart",
    description="从购物车移除某商品。",
    parameters={
        "type": "object",
        "properties": {"product_id": {"type": "string"}},
        "required": ["product_id"],
    },
    return_type="dict",
    func=remove_from_cart,
    permissions=PermissionLevel.WRITE,
))
