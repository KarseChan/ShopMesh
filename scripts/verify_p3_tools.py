"""Verify P3 deterministic tools: reorder_from_history + assemble_meal.

Seeds a paid order (so reorder has history), then exercises both tools.
No LLM. Prereqs: infra (Redis + Postgres) + mock_merchants/mock_dishes generated.

Usage:
    python scripts/verify_p3_tools.py
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.auth.context import set_context_user_id
from src.skills import cart_store, order_service, payment_service
from src.tools.instant_order_tools import assemble_meal, get_merchant_menu, reorder_from_history

_MERCHANTS = Path(__file__).resolve().parent.parent / "data" / "mock_merchants.json"


async def run():
    uid = f"verify_p3_{uuid.uuid4().hex[:6]}"
    set_context_user_id(uid)
    merchants = json.load(open(_MERCHANTS, encoding="utf-8"))
    fastfood = next(m for m in merchants if m["category"] == "快餐")
    mid = fastfood["merchant_id"]
    print(f"用户: {uid}\n门店(快餐): {fastfood['name']} ({mid})\n")

    # ── assemble_meal:预算内凑单(主食+饮品)──
    print("[assemble_meal] 预算 ¥40 组套餐:")
    res = await assemble_meal(mid, budget=40, add_to_cart=True)
    assert res.get("ok"), res
    for c in res["combo"]:
        print(f"    [{c['category']}] {c['name']}  ¥{c['price']}")
    print(f"    → {res['note']}\n")

    # 预算不足的边界
    low = await assemble_meal(mid, budget=3)
    print(f"[assemble_meal] 预算 ¥3(不足): ok={low.get('ok')}  {low.get('message','')}\n")

    # ── 下单 + 支付,制造一条历史订单 ──
    order = await order_service.create_order_from_cart(uid, f"s_{uid}",
                                                       idempotency_key=f"idem_{uuid.uuid4().hex[:8]}")
    assert order.get("ok"), order
    oid = order["order_id"]
    sess = await payment_service.create_session(oid, order["total_price"])
    await payment_service.simulate_payment(sess["payment_ref"])
    print(f"[seed] 已下单并支付历史订单 {oid}（¥{order['total_price']}）\n")

    # ── reorder_from_history:再来一单 ──
    await cart_store.clear(uid)
    print("[reorder_from_history] 再来一单:")
    r = await reorder_from_history()
    assert r.get("ok"), r
    for it in r["added"]:
        print(f"    + {it['name']} ×{it['qty']}  ¥{it['price']}")
    print(f"    → {r['note']}")
    print(f"    购物车: {r['cart_count']} 件 / ¥{r['cart_total']}\n")

    # 按门店过滤的再来一单
    r2 = await reorder_from_history(merchant_id=mid)
    print(f"[reorder_from_history] 指定门店 {mid}: ok={r2.get('ok')} 加入 {len(r2.get('added', []))} 件")

    print("\nP3 确定性工具验证通过 ✅")


if __name__ == "__main__":
    asyncio.run(run())
