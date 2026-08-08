"""Verify the 秒送 点单闭环 (P2): 组单 → 下单 → 支付 → 履约,全程确定性、无 LLM.

Reuses cart_store + order_service + payment_service verbatim (dishes resolve via
item_catalog). Proves: 原子库存预占 / 服务端重算价 / 幂等 / HMAC 验签 webhook /
履约状态机(备餐→配送中→已送达).

Prereqs: infra (Redis 16379 + Postgres 15432 with orders schema) up, and
    python scripts/generate_mock_merchants.py
    python scripts/generate_mock_dishes.py

Usage:
    python scripts/verify_instant_order_flow.py
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.skills import cart_store, order_service, payment_service
from src.skills.item_catalog import get_menu

_MERCHANTS = Path(__file__).resolve().parent.parent / "data" / "mock_merchants.json"


async def run():
    uid = f"verify_flow_{uuid.uuid4().hex[:6]}"
    session_id = f"s_{uid}"

    # 选一家奶茶店
    merchants = json.load(open(_MERCHANTS, encoding="utf-8"))
    store = next(m for m in merchants if m["category"] == "奶茶")
    mid = store["merchant_id"]
    print(f"用户: {uid}\n门店: {store['name']} ({mid})\n")

    # 1) 拉菜单
    menu = get_menu(mid)
    print(f"[组单] 门店菜单 {len(menu)} 道:")
    for d in menu:
        print(f"    {d['product_id']}  {d['name']}  ¥{d['price']}  库存{d['stock']}")
    picks = menu[:2]
    print(f"\n[组单] 选 {len(picks)} 道加入购物车:")
    await cart_store.clear(uid)
    for d in picks:
        await cart_store.add_item(uid, d["product_id"], d["name"], d["price"], 1)
        print(f"    + {d['name']} ×1")
    stock_before = {d["product_id"]: await order_service.get_stock(d["product_id"]) for d in picks}

    # 2) 下单(确定性:重算价 + 原子库存 + 幂等 + 落库)
    idem = f"idem_{uuid.uuid4().hex[:8]}"
    order = await order_service.create_order_from_cart(uid, session_id, idempotency_key=idem)
    assert order.get("ok"), order
    oid = order["order_id"]
    print(f"\n[下单] {oid}  状态={order['status']}  合计=¥{order['total_price']}")
    for it in order["items"]:
        print(f"    {it['name']} ×{it['qty']}  ¥{it['price']}  (merchant={it.get('merchant_id')}, type={it.get('item_type')})")

    # 库存已原子预占
    for d in picks:
        after = await order_service.get_stock(d["product_id"])
        print(f"    库存预占: {d['name']} {stock_before[d['product_id']]} → {after}")

    # 幂等:同 idem 再下一次 → 返回同一订单,不重复扣库存
    dup = await order_service.create_order_from_cart(uid, session_id, idempotency_key=idem)
    print(f"[幂等] 同 idempotency_key 重复下单 → order_id={dup['order_id']} idempotent={dup.get('idempotent')}")
    assert dup["order_id"] == oid

    # 3) 支付(创建会话 → mock 支付方生成已签名 webhook → 验签 → paid)
    sess = await payment_service.create_session(oid, order["total_price"])
    print(f"\n[支付] 会话 {sess['payment_ref']}  跳转={sess['pay_url']}")
    paid = await payment_service.simulate_payment(sess["payment_ref"])
    print(f"[支付] webhook 验签处理 → ok={paid.get('ok')} status={paid.get('status')}")
    o = await order_service.get_order(oid)
    assert o["status"] == "paid", o
    print(f"[支付] 订单状态 = {o['status']}")

    # 伪造回调(错误签名)应被拒
    bad = await payment_service.handle_webhook(b'{"event":"payment.succeeded","payment_ref":"x"}', "deadbeef")
    print(f"[安全] 伪造未验签 webhook → ok={bad.get('ok')} (应为 False)")

    # 4) 履约状态机:备餐 → 配送中 → 已送达
    print("\n[履约] 手动推进:")
    for _ in range(4):
        adv = await order_service.advance_fulfillment(oid)
        label = order_service.FULFILLMENT_LABELS.get(adv.get("status"), adv.get("status"))
        print(f"    → {adv.get('status')} ({label})  done={adv.get('done')}")
        if adv.get("done"):
            break

    final = await order_service.get_order(oid)
    print(f"\n[完成] 订单 {oid} 终态 = {final['status']}")
    print("闭环验证通过 ✅")


if __name__ == "__main__":
    asyncio.run(run())
