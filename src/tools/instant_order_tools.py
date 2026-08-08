"""秒送 agentic 工具 (P3):再来一单 / 预算内凑单 / 门店菜单.

设计对齐方案 4.4 + 灵魂边界:
- 工具内核**确定性**(reorder = 读历史重组购物车;assemble_meal = 预算背包凑单),
  可独立验证、demo 安全。
- LLM 只在上层 ReAct 里做**对话理解 + 编排**(把模糊口语转成结构化约束 → 调这些工具),
  这是方案里 LLM 真正有增量价值的点(多步权衡)。
- 下单仍是 SENSITIVE 人工闸门:这些工具只组购物车,绝不自主下单。

user_id 一律从认证上下文取,不接受 LLM 指定(防越权,同 cart_tools)。
"""

from src.auth.context import get_context_user_id
from src.observability.logger import get_logger
from src.skills import cart_store, order_service
from src.skills.item_catalog import find_item, get_menu
from src.skills.schema import PermissionLevel
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("instant_order_tools")


def _uid() -> str | None:
    return get_context_user_id() or None


# ── 菜品分类(凑单用):主食 / 小食 / 饮品 / 其它 ──
_MAIN_KW = ("堡", "饭", "鸡排", "鸡腿", "炸鸡", "面", "套餐", "盖饭")
_SNACK_KW = ("薯条", "小食", "鸡块", "薯", "圈")
_DRINK_KW = ("奶茶", "茶", "可乐", "咖啡", "饮", "葡萄", "奶盖", "柠檬", "仙草", "波波", "拿铁")


def _dish_category(dish: dict) -> str:
    name = dish.get("name", "")
    if any(k in name for k in _MAIN_KW):
        return "主食"
    if any(k in name for k in _SNACK_KW):
        return "小食"
    if any(k in name for k in _DRINK_KW):
        return "饮品"
    return "其它"


# ── 再来一单 ──

async def reorder_from_history(merchant_id: str | None = None) -> dict:
    """把用户最近一单(可按门店过滤)的菜品重新加入购物车。

    确定性:读订单历史 → 按当前价/库存重新解析每个菜品 → 重组购物车。
    下架/售罄的菜品会跳过并在 note 里说明。不自动下单。
    """
    uid = _uid()
    if not uid:
        return {"ok": False, "message": "请先登录后再使用"}

    orders = await order_service.list_orders(uid, limit=50)
    # 只看含秒送菜品的订单;可选按门店过滤
    def _is_target(o: dict) -> bool:
        items = o.get("items", [])
        if not any(it.get("item_type") == "dish" for it in items):
            return False
        if merchant_id:
            return any(it.get("merchant_id") == merchant_id for it in items)
        return True

    target = next((o for o in orders if _is_target(o)), None)
    if not target:
        return {"ok": False, "message": "没有找到可再来一单的历史订单"}

    await cart_store.clear(uid)
    added, skipped = [], []
    store_name = ""
    for it in target["items"]:
        pid = it["product_id"]
        cur = find_item(pid)
        if not cur or int(cur.get("stock", 0)) <= 0:
            skipped.append(it.get("name", pid))
            continue
        price = float(cur.get("final_price") or cur.get("price", 0))
        qty = int(it.get("qty", 1))
        await cart_store.add_item(uid, pid, cur.get("name", "商品"), price, qty)
        store_name = store_name or cur.get("brand", "")
        added.append({"name": cur.get("name"), "qty": qty, "price": price})

    cart = await cart_store.get_cart(uid)
    note = f"已按上次订单重组购物车（{store_name}）"
    if skipped:
        note += f"；{len(skipped)} 个已下架/售罄未加入：{'、'.join(skipped)}"
    logger.info("reorder_done", user_id=uid, order_id=target["order_id"],
                added=len(added), skipped=len(skipped))
    return {"ok": True, "source_order_id": target["order_id"], "added": added,
            "skipped": skipped, "cart_count": cart["count"], "cart_total": cart["total"],
            "note": note}


# ── 预算内凑单 ──

async def assemble_meal(merchant_id: str, budget: float,
                        want: list[str] | None = None,
                        add_to_cart: bool = False) -> dict:
    """在某门店菜单里,预算内组一份套餐(主食+饮品等),尽量用满预算.

    确定性背包:按目标品类各选一道"预算内最贵"的(凑单),再用剩余预算补一道。
    这是"多步权衡"的确定性内核;LLM 负责把模糊口语转成 merchant_id/budget/want。

    Args:
        merchant_id: 门店 id(先由 nearby_merchant_search 得到)。
        budget: 预算上限(元)。
        want: 目标品类,如 ["主食","饮品"];缺省按菜单里可得品类。
        add_to_cart: True 则把组好的套餐加入购物车(仍需人工确认下单)。
    """
    uid = _uid()
    menu = get_menu(merchant_id)
    if not menu:
        return {"ok": False, "message": f"门店 {merchant_id} 没有可点菜品"}

    by_cat: dict[str, list[dict]] = {}
    for d in menu:
        by_cat.setdefault(_dish_category(d), []).append(d)
    for cat in by_cat:
        by_cat[cat].sort(key=lambda x: x.get("price", 0))  # 升序

    # 目标品类:优先用户指定,否则按 主食→饮品→小食→其它 取存在的
    if want:
        target_cats = [c for c in want if c in by_cat]
    else:
        target_cats = [c for c in ("主食", "饮品", "小食", "其它") if c in by_cat]
    if not target_cats:
        target_cats = list(by_cat.keys())

    combo: list[dict] = []
    remaining = float(budget)
    # 每个目标品类挑"预算内最贵"的一道(凑单,尽量用满预算)
    for cat in target_cats:
        affordable = [d for d in by_cat[cat] if d.get("price", 0) <= remaining]
        if affordable:
            pick = affordable[-1]  # 最贵的可负担
            combo.append(pick)
            remaining -= pick["price"]

    # 用剩余预算再补:任意品类里预算内最贵且未选过的
    chosen_ids = {d["product_id"] for d in combo}
    while True:
        cands = [d for d in menu
                 if d["product_id"] not in chosen_ids and d.get("price", 0) <= remaining]
        if not cands:
            break
        pick = max(cands, key=lambda x: x.get("price", 0))
        combo.append(pick)
        chosen_ids.add(pick["product_id"])
        remaining -= pick["price"]

    if not combo:
        cheapest = min(menu, key=lambda x: x.get("price", 0))
        return {"ok": False,
                "message": f"预算 ¥{budget} 不足以点任何一道（最低 ¥{cheapest['price']}）",
                "cheapest": {"name": cheapest["name"], "price": cheapest["price"]}}

    total = round(sum(d["price"] for d in combo), 2)
    combo_slim = [{"product_id": d["product_id"], "name": d["name"],
                   "price": d["price"], "category": _dish_category(d)} for d in combo]

    added = False
    if add_to_cart and uid:
        await cart_store.clear(uid)
        for d in combo:
            await cart_store.add_item(uid, d["product_id"], d["name"], d["price"], 1)
        added = True

    note = f"已在 ¥{budget} 预算内组 {len(combo)} 件，合计 ¥{total}（剩 ¥{round(remaining, 2)}）"
    logger.info("assemble_meal_done", merchant_id=merchant_id, budget=budget,
                items=len(combo), total=total, added_to_cart=added)
    return {"ok": True, "merchant_id": merchant_id, "budget": budget,
            "combo": combo_slim, "total": total, "remaining": round(remaining, 2),
            "added_to_cart": added, "note": note}


async def get_merchant_menu(merchant_id: str) -> dict:
    """查询某门店的可点菜品(菜单)。组单/凑单前浏览用。"""
    dishes = get_menu(merchant_id)
    slim = [{"product_id": d["product_id"], "name": d["name"], "price": d["price"],
             "category": _dish_category(d)} for d in dishes]
    return {"ok": True, "merchant_id": merchant_id, "count": len(slim), "dishes": slim}


# ── 注册 ──
tool_registry.register(ToolDef(
    name="reorder_from_history",
    description="再来一单:把用户最近一单(可指定门店)的菜品重新加入购物车。"
                "用户说'再来一单/老样子/上次那个'时调用。不自动下单,仍需用户确认。",
    parameters={
        "type": "object",
        "properties": {
            "merchant_id": {"type": "string", "description": "可选,只重下该门店的历史单"},
        },
    },
    return_type="dict",
    func=reorder_from_history,
    permissions=PermissionLevel.WRITE,
))

tool_registry.register(ToolDef(
    name="assemble_meal",
    description="预算内凑单:在指定门店菜单里,按预算组一份套餐(主食+饮品等),尽量用满预算。"
                "用户说'帮我组个X块的套餐/凑一单'时调用。先用 nearby_merchant_search 拿到 merchant_id。",
    parameters={
        "type": "object",
        "properties": {
            "merchant_id": {"type": "string", "description": "门店 id"},
            "budget": {"type": "number", "description": "预算上限(元)"},
            "want": {"type": "array", "items": {"type": "string"},
                     "description": "目标品类,如 ['主食','饮品'],可省略"},
            "add_to_cart": {"type": "boolean", "description": "是否直接加入购物车,默认 false", "default": False},
        },
        "required": ["merchant_id", "budget"],
    },
    return_type="dict",
    func=assemble_meal,
    permissions=PermissionLevel.WRITE,
))

tool_registry.register(ToolDef(
    name="get_merchant_menu",
    description="查询某门店的可点菜品(菜单)。组单/凑单前浏览。",
    parameters={
        "type": "object",
        "properties": {"merchant_id": {"type": "string"}},
        "required": ["merchant_id"],
    },
    return_type="dict",
    func=get_merchant_menu,
    permissions=PermissionLevel.READ,
))
