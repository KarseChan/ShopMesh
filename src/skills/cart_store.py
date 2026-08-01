"""购物车存储 —— Redis 后端(会话级持久,TTL 7 天)。

设计要点:
- key = cart:{user_id};每个 user_id 一个 Redis hash,field = product_id。
- user_id 一律由调用方从**认证上下文**传入,绝不接受 LLM 指定(防越权操作他人购物车)。
- 存 price_snapshot 仅用于展示;下单时必须用服务端实时价重新校验(见 purchase-feature-design.md)。
"""

import json
import time

from src.db.redis_client import get_redis
from src.observability.logger import get_logger

logger = get_logger("cart_store")

_TTL = 7 * 24 * 3600  # 7 天


def _key(user_id: str) -> str:
    return f"cart:{user_id}"


async def add_item(user_id: str, product_id: str, name: str, price: float, qty: int = 1) -> dict:
    """加入购物车(已存在则累加数量)。返回该条目最新状态。"""
    r = get_redis()
    key = _key(user_id)
    raw = await r.hget(key, product_id)
    item = json.loads(raw) if raw else {
        "product_id": product_id, "name": name, "price": price, "qty": 0,
    }
    item["qty"] = max(0, int(item.get("qty", 0)) + int(qty))
    item["name"] = name
    item["price"] = price
    item["updated_at"] = int(time.time())
    if item["qty"] <= 0:
        await r.hdel(key, product_id)
    else:
        await r.hset(key, product_id, json.dumps(item, ensure_ascii=False))
        await r.expire(key, _TTL)
    logger.info("cart_add", user_id=user_id, product_id=product_id, qty=item["qty"])
    return item


async def get_cart(user_id: str) -> dict:
    """返回 {items, count, total}。"""
    r = get_redis()
    raw = await r.hgetall(_key(user_id))
    items = [json.loads(v) for v in raw.values()]
    items.sort(key=lambda x: x.get("updated_at", 0))
    total = round(sum(float(i["price"]) * int(i["qty"]) for i in items), 2)
    count = sum(int(i["qty"]) for i in items)
    return {"items": items, "count": count, "total": total}


async def set_qty(user_id: str, product_id: str, qty: int) -> bool:
    """设置某商品数量;qty<=0 视为移除。返回是否命中该商品。"""
    r = get_redis()
    key = _key(user_id)
    if int(qty) <= 0:
        removed = await r.hdel(key, product_id)
        return bool(removed)
    raw = await r.hget(key, product_id)
    if not raw:
        return False
    item = json.loads(raw)
    item["qty"] = int(qty)
    item["updated_at"] = int(time.time())
    await r.hset(key, product_id, json.dumps(item, ensure_ascii=False))
    await r.expire(key, _TTL)
    return True


async def remove_item(user_id: str, product_id: str) -> bool:
    r = get_redis()
    removed = await r.hdel(_key(user_id), product_id)
    return bool(removed)


async def clear(user_id: str) -> None:
    r = get_redis()
    await r.delete(_key(user_id))
