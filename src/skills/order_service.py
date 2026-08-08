"""订单服务(购物功能 P2)—— 确定性、落库、库存安全、幂等。

设计对应 docs/purchase-feature-design.md:
- 库存:Redis 原子 DECRBY 预占,防超卖(从商品 JSON 的 stock 种子)。
- 订单:落 Postgres orders 表(items_json 存购物车明细),状态机。
- 幂等:idempotency_key 唯一,同键重复下单返回同一订单。
- 下单前服务端重算价格(不信购物车快照)。
- 全流程确定性代码,不经过 LLM(LLM 只在上层"提议",人工确认后才调用本服务)。
"""

import asyncio
import json
import time
import uuid

from src.db.redis_client import get_redis
from src.observability.logger import get_logger
from src.skills import cart_store

logger = get_logger("order_service")

def _product(product_id: str) -> dict | None:
    # Resolves products AND 秒送 dishes (item_catalog), so this order flow works
    # for dishes verbatim. Kept as a thin delegator to avoid a second cache.
    from src.skills.item_catalog import find_item
    return find_item(product_id)


# ── 库存(Redis 原子预占,防超卖)──

def _stock_key(pid: str) -> str:
    return f"stock:{pid}"


async def _reserve(product_id: str, qty: int) -> bool:
    """原子预占库存。成功 True;不足则回滚并 False。"""
    r = get_redis()
    key = _stock_key(product_id)
    # 种子:仅当不存在时用商品 JSON 的 stock 初始化
    p = _product(product_id)
    seed = int(p.get("stock", 0)) if p else 0
    await r.set(key, seed, nx=True)
    new_val = await r.decrby(key, qty)
    if new_val < 0:
        await r.incrby(key, qty)  # 回滚
        return False
    return True


async def _release(product_id: str, qty: int) -> None:
    r = get_redis()
    await r.incrby(_stock_key(product_id), qty)


async def get_stock(product_id: str) -> int:
    r = get_redis()
    v = await r.get(_stock_key(product_id))
    if v is None:
        p = _product(product_id)
        return int(p.get("stock", 0)) if p else 0
    return int(v)


# ── 订单落库(同步 DB,用 to_thread 包）──

def _db_find_by_idem(idem: str) -> dict | None:
    from sqlmodel import select
    from src.db.engine import get_session
    from src.db.models import Order
    with get_session() as s:
        o = s.exec(select(Order).where(Order.idempotency_key == idem)).first()
        return _order_to_dict(o) if o else None


def _db_insert(order: dict) -> None:
    from src.db.engine import get_session
    from src.db.models import Order
    with get_session() as s:
        s.add(Order(
            tenant_id=order.get("tenant_id", ""),
            order_id=order["order_id"],
            session_id=order["session_id"],
            user_id=order["user_id"],
            product_id=order["items"][0]["product_id"] if order["items"] else "",
            quantity=sum(i["qty"] for i in order["items"]),
            total_price=order["total_price"],
            items_json=json.dumps(order["items"], ensure_ascii=False),
            idempotency_key=order.get("idempotency_key"),
            status=order["status"],
        ))
        s.commit()


def _db_get(order_id: str):
    from sqlmodel import select
    from src.db.engine import get_session
    from src.db.models import Order
    with get_session() as s:
        return s.exec(select(Order).where(Order.order_id == order_id)).first()


def _db_set_status(order_id: str, status: str) -> dict | None:
    from src.db.engine import get_session
    from src.db.models import Order
    from sqlmodel import select
    with get_session() as s:
        o = s.exec(select(Order).where(Order.order_id == order_id)).first()
        if not o:
            return None
        o.status = status
        s.add(o)
        s.commit()
        s.refresh(o)
        return _order_to_dict(o)


def _order_to_dict(o) -> dict:
    return {
        "order_id": o.order_id, "user_id": o.user_id, "session_id": o.session_id,
        "status": o.status, "total_price": o.total_price,
        "items": json.loads(o.items_json) if o.items_json else [],
        "idempotency_key": o.idempotency_key,
        "created_at": o.created_at.isoformat() if o.created_at else "",
    }


def _db_list(user_id: str, limit: int = 50) -> list[dict]:
    from sqlmodel import select, col
    from src.db.engine import get_session
    from src.db.models import Order
    with get_session() as s:
        rows = s.exec(
            select(Order).where(Order.user_id == user_id)
            .order_by(col(Order.created_at).desc()).limit(limit)
        ).all()
        return [_order_to_dict(o) for o in rows]


# ── 对外:创建/取消/查询 ──

async def create_order_from_cart(user_id: str, session_id: str,
                                 idempotency_key: str | None = None,
                                 tenant_id: str = "") -> dict:
    """从购物车确定性地创建订单。幂等 + 服务端重算价 + 原子库存预占。"""
    # 1. 幂等
    if idempotency_key:
        existing = await asyncio.to_thread(_db_find_by_idem, idempotency_key)
        if existing:
            logger.info("order_idempotent_hit", idempotency_key=idempotency_key,
                        order_id=existing["order_id"])
            return {"ok": True, "idempotent": True, **existing}

    # 2. 读购物车
    cart = await cart_store.get_cart(user_id)
    if not cart["items"]:
        return {"ok": False, "message": "购物车为空,无法下单"}

    # 3. 服务端重算价格(不信购物车快照),构建订单明细
    items = []
    total = 0.0
    for ci in cart["items"]:
        pid = ci["product_id"]
        p = _product(pid)
        if not p:
            return {"ok": False, "message": f"商品 {pid} 已下架"}
        price = float(p.get("final_price") or p.get("price", 0))  # 实时价
        qty = int(ci["qty"])
        item = {"product_id": pid, "name": p.get("name", "商品"), "price": price, "qty": qty}
        # 秒送:带上门店/菜品上下文(履约在门店备餐,订单历史展示门店)
        if p.get("merchant_id"):
            item["merchant_id"] = p["merchant_id"]
        if p.get("item_type"):
            item["item_type"] = p["item_type"]
        items.append(item)
        total += price * qty
    total = round(total, 2)

    # 4. 原子预占库存;任一失败则回滚已占
    reserved = []
    for it in items:
        ok = await _reserve(it["product_id"], it["qty"])
        if not ok:
            for r in reserved:
                await _release(r["product_id"], r["qty"])
            return {"ok": False, "message": f"「{it['name']}」库存不足,下单失败"}
        reserved.append(it)

    # 5. 落库
    order = {
        "order_id": f"ORD-{uuid.uuid4().hex[:12].upper()}",
        "user_id": user_id, "session_id": session_id, "tenant_id": tenant_id,
        "items": items, "total_price": total,
        "status": "awaiting_payment", "idempotency_key": idempotency_key,
        "created_at": int(time.time()),
    }
    try:
        await asyncio.to_thread(_db_insert, order)
    except Exception as e:
        for it in items:  # 落库失败,释放库存
            await _release(it["product_id"], it["qty"])
        logger.error("order_insert_failed", error=str(e))
        return {"ok": False, "message": "下单失败,请重试"}

    # 6. 清空购物车
    await cart_store.clear(user_id)
    logger.info("order_created", order_id=order["order_id"], total=total, items=len(items))
    return {"ok": True, "order_id": order["order_id"], "status": order["status"],
            "total_price": total, "items": items}


async def cancel_order(order_id: str, user_id: str | None = None) -> dict:
    """取消订单并释放库存(仅未支付/未发货可取消)。"""
    o = await asyncio.to_thread(_db_get, order_id)
    if not o:
        return {"ok": False, "message": "订单不存在"}
    if user_id and o.user_id != user_id:
        return {"ok": False, "message": "无权操作该订单"}
    if o.status not in ("created", "awaiting_payment"):
        return {"ok": False, "message": f"当前状态({o.status})不可取消"}
    items = json.loads(o.items_json) if o.items_json else []
    for it in items:
        await _release(it["product_id"], int(it["qty"]))
    updated = await asyncio.to_thread(_db_set_status, order_id, "cancelled")
    logger.info("order_cancelled", order_id=order_id, released=len(items))
    return {"ok": True, "order_id": order_id, "status": "cancelled"}


async def mark_paid(order_id: str) -> dict:
    """支付成功:awaiting_payment → paid(幂等)。库存在下单时已预占,此处不再变动。

    仅应由**验签通过的支付 webhook** 调用,不能由前端直接触发。
    """
    o = await asyncio.to_thread(_db_get, order_id)
    if not o:
        return {"ok": False, "message": "订单不存在"}
    if o.status == "paid":
        return {"ok": True, "order_id": order_id, "status": "paid", "idempotent": True}
    if o.status != "awaiting_payment":
        return {"ok": False, "message": f"状态({o.status})不可支付"}
    await asyncio.to_thread(_db_set_status, order_id, "paid")
    logger.info("order_paid", order_id=order_id)
    return {"ok": True, "order_id": order_id, "status": "paid"}


def list_expired_awaiting(older_than_sec: int) -> list[str]:
    """返回超过 older_than_sec 未支付(awaiting_payment)的订单号 —— 供 Celery 超时取消。"""
    from datetime import datetime, timedelta
    from sqlmodel import select
    from src.db.engine import get_session
    from src.db.models import Order
    cutoff = datetime.utcnow() - timedelta(seconds=older_than_sec)
    with get_session() as s:
        rows = s.exec(
            select(Order).where(Order.status == "awaiting_payment").where(Order.created_at < cutoff)
        ).all()
        return [o.order_id for o in rows]


async def refund_order(order_id: str, user_id: str | None = None) -> dict:
    """退款:已支付订单 paid → refunded,并释放库存。

    真实场景需调用支付网关退款 API;此处 mock 直接转状态 + 释放库存。
    """
    o = await asyncio.to_thread(_db_get, order_id)
    if not o:
        return {"ok": False, "message": "订单不存在"}
    if user_id and o.user_id != user_id:
        return {"ok": False, "message": "无权操作该订单"}
    if o.status != "paid":
        return {"ok": False, "message": f"当前状态({o.status})不可退款"}
    items = json.loads(o.items_json) if o.items_json else []
    for it in items:
        await _release(it["product_id"], int(it["qty"]))
    await asyncio.to_thread(_db_set_status, order_id, "refunded")
    logger.info("order_refunded", order_id=order_id, released=len(items))
    return {"ok": True, "order_id": order_id, "status": "refunded"}


# ── 秒送履约状态机(paid → 备餐 → 配送中 → 已送达)──
# 复用订单 status 字段(自由字符串,无需新迁移)。真实场景由门店/骑手事件驱动;
# 此处 mock:一个确定性 advance 步进 + Celery 定时自动推进(见 cleanup_tasks)。

# 履约推进链:每个状态 → 下一个状态
_FULFILLMENT_NEXT = {
    "paid": "preparing",        # 备餐
    "preparing": "delivering",  # 配送中
    "delivering": "delivered",  # 已送达(终态)
}

# 处于履约中(可被自动推进)的状态
FULFILLMENT_ACTIVE = ("paid", "preparing", "delivering")

# 履约状态的人读名(供前端/文案)
FULFILLMENT_LABELS = {
    "paid": "已支付",
    "preparing": "备餐中",
    "delivering": "配送中",
    "delivered": "已送达",
}


async def advance_fulfillment(order_id: str, user_id: str | None = None) -> dict:
    """把订单沿履约链推进一步(paid→备餐→配送中→已送达)。幂等到终态。

    只在已支付后可推进;未支付/已取消/已退款状态拒绝。
    """
    o = await asyncio.to_thread(_db_get, order_id)
    if not o:
        return {"ok": False, "message": "订单不存在"}
    if user_id and o.user_id != user_id:
        return {"ok": False, "message": "无权操作该订单"}
    if o.status == "delivered":
        return {"ok": True, "order_id": order_id, "status": "delivered", "done": True}
    nxt = _FULFILLMENT_NEXT.get(o.status)
    if not nxt:
        return {"ok": False, "message": f"状态({o.status})不在履约链中"}
    updated = await asyncio.to_thread(_db_set_status, order_id, nxt)
    logger.info("order_fulfillment_advanced", order_id=order_id,
                **{"from": o.status, "to": nxt})
    return {"ok": True, "order_id": order_id, "status": nxt,
            "status_label": FULFILLMENT_LABELS.get(nxt, nxt),
            "done": nxt == "delivered"}


def list_active_fulfillment(limit: int = 200) -> list[str]:
    """返回处于履约中(paid/preparing/delivering)的订单号 —— 供 Celery 自动推进。"""
    from sqlmodel import select, col
    from src.db.engine import get_session
    from src.db.models import Order
    with get_session() as s:
        rows = s.exec(
            select(Order).where(col(Order.status).in_(FULFILLMENT_ACTIVE)).limit(limit)
        ).all()
        return [o.order_id for o in rows]


async def list_orders(user_id: str, limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(_db_list, user_id, limit)


async def get_order(order_id: str) -> dict | None:
    o = await asyncio.to_thread(_db_get, order_id)
    return _order_to_dict(o) if o else None
