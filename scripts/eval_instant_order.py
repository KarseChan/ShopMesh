"""秒送质量 Eval Harness —— 量化就近约束/凑单/再来一单的质量。

延续 scripts/eval_recommendation.py 的思路(数据驱动、可复现):直接喂结构化约束
调**确定性**秒送链路(nearby_merchant_search / assemble_meal / reorder_from_history),
不经 LLM,固定 now_hour 保证可复现、可进 CI。

指标:
  recall  : non_empty / geo_feasible(距离≤配送半径)/ open_now(营业中)/
            eta_ok(ETA≤要求)/ category / budget
  degrade : 无解约束(如 5 分钟送到)应返回空 + 明确理由,而非返回不达标门店 → 空即正确
  assemble: budget_ok(组合总价≤预算)/ well_used(用满预算≥50%)
  reorder : 重组购物车的商品集合 == 上次订单

用法:PYTHONPATH=. python scripts/eval_instant_order.py
依赖:Qdrant + Ollama(embedding)+ Redis + Postgres。需先 build 门店索引 + 生成菜品。
"""

import asyncio
import json
import sys
import uuid
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.retrieval.geo import DEMO_USER_LOCATION

# 固定"当前时间"保证可复现:14:00 白天,所有 10:00-22:00 门店营业。
FIXED_NOW = 14

_MERCHANTS = Path(__file__).resolve().parent.parent / "data" / "mock_merchants.json"


def _merchant_ids() -> dict:
    ms = json.load(open(_MERCHANTS, encoding="utf-8"))
    tea = next(m["merchant_id"] for m in ms if m["category"] == "奶茶")
    fast = next(m["merchant_id"] for m in ms if m["category"] == "快餐")
    return {"奶茶": tea, "快餐": fast}


# ── recall / degrade 用例:(name, kind, constraints, expect) ──
def _recall_cases():
    return [
        ("附近奶茶30分钟", "recall",
         {"merchant_category": "奶茶", "max_delivery_minutes": 30},
         {"category": "奶茶", "max_eta": 30}),
        ("附近快餐半小时人均30", "recall",
         {"merchant_category": "快餐", "max_delivery_minutes": 30, "budget": 30},
         {"category": "快餐", "max_eta": 30, "budget": 30}),
        ("附近奶茶不限时效", "recall",
         {"merchant_category": "奶茶"},
         {"category": "奶茶"}),
        ("附近奶茶5分钟(无解)", "degrade",
         {"merchant_category": "奶茶", "max_delivery_minutes": 5}, {}),
        ("附近药店人均1元(无解)", "degrade",
         {"merchant_category": "药店", "budget": 1}, {}),
    ]


def _check_recall(full: list[dict], expect: dict) -> dict:
    res = {"non_empty": len(full) > 0}
    if not full:
        return res
    res["geo_feasible"] = all(
        m.get("distance_km", 0) <= float(m.get("delivery_radius_km", 3)) for m in full)
    res["open_now"] = all(
        m.get("open_hour", 0) <= FIXED_NOW < m.get("close_hour", 24) for m in full)
    if expect.get("max_eta") is not None:
        res["eta_ok"] = all(m.get("delivery_minutes", 0) <= expect["max_eta"] for m in full)
    if expect.get("category"):
        res["category"] = all(m.get("category") == expect["category"] for m in full)
    if expect.get("budget") is not None:
        res["budget"] = all(m.get("avg_price", 0) <= expect["budget"] for m in full)
    return res


async def _run_recall(cases, rows, agg, degrade):
    from src.tools.merchant_search import nearby_merchant_search
    for name, kind, c, expect in cases:
        r = await nearby_merchant_search(
            location=DEMO_USER_LOCATION, semantic_query=name,
            merchant_category=c.get("merchant_category"),
            max_delivery_minutes=c.get("max_delivery_minutes"),
            budget=c.get("budget"), now_hour=FIXED_NOW)
        full = r.get("_full", [])
        if kind == "degrade":
            ok = (len(full) == 0)  # 无解约束应返回空,而非不达标门店
            degrade["total"] += 1
            degrade["ok"] += 1 if ok else 0
            rows.append((name, len(full), {"empty_as_expected": ok}, "degrade"))
        else:
            checks = _check_recall(full, expect)
            rows.append((name, len(full), checks, "recall"))
            for k, v in checks.items():
                agg.setdefault(k, []).append(v)


async def _run_assemble(rows, agg):
    from src.tools.instant_order_tools import assemble_meal
    ids = _merchant_ids()
    cases = [
        ("奶茶店凑¥30", ids["奶茶"], 30),
        ("快餐店凑¥40", ids["快餐"], 40),
    ]
    for name, mid, budget in cases:
        r = await assemble_meal(mid, budget=budget, add_to_cart=False)
        if not r.get("ok"):
            checks = {"budget_ok": False, "well_used": False}
        else:
            total = r["total"]
            checks = {"budget_ok": total <= budget,
                      "well_used": total >= 0.5 * budget}
        rows.append((name, len(r.get("combo", [])), checks, "assemble"))
        for k, v in checks.items():
            agg.setdefault(f"assemble_{k}", []).append(v)

    # 预算不足应被拒绝(非硬凑)
    low = await assemble_meal(ids["奶茶"], budget=1, add_to_cart=False)
    ok = not low.get("ok")
    rows.append(("凑¥1(应拒绝)", 0, {"refused_as_expected": ok}, "assemble"))
    agg.setdefault("assemble_refuse_unsat", []).append(ok)


async def _run_reorder(rows, agg):
    from src.auth.context import set_context_user_id
    from src.skills import cart_store, order_service
    from src.skills.item_catalog import get_menu

    uid = f"eval_reorder_{uuid.uuid4().hex[:6]}"
    set_context_user_id(uid)
    ids = _merchant_ids()
    menu = get_menu(ids["奶茶"])[:2]
    seeded_ids = {d["product_id"] for d in menu}

    await cart_store.clear(uid)
    for d in menu:
        await cart_store.add_item(uid, d["product_id"], d["name"], d["price"], 1)
    order = await order_service.create_order_from_cart(
        uid, f"s_{uid}", idempotency_key=f"idem_{uuid.uuid4().hex[:8]}")

    from src.tools.instant_order_tools import reorder_from_history
    await cart_store.clear(uid)
    await reorder_from_history()
    cart = await cart_store.get_cart(uid)
    rebuilt_ids = {it["product_id"] for it in cart["items"]}
    ok = rebuilt_ids == seeded_ids and order.get("ok")
    rows.append(("再来一单(集合一致)", len(rebuilt_ids), {"reorder_match": ok}, "reorder"))
    agg.setdefault("reorder_match", []).append(ok)


async def main():
    rows, agg = [], {}
    degrade = {"total": 0, "ok": 0}

    await _run_recall(_recall_cases(), rows, agg, degrade)
    await _run_assemble(rows, agg)
    await _run_reorder(rows, agg)

    print("\n" + "=" * 76)
    print(f"秒送质量 Eval 报告 (now_hour={FIXED_NOW:02d}:00, 固定以复现)")
    print("=" * 76)
    for name, n, checks, kind in rows:
        marks = "  ".join(f"{k}={'✓' if v else '✗'}" for k, v in checks.items())
        print(f"  [{kind:8s}] {name:20s} n={n:<3d} {marks}")

    print("-" * 76)
    print("各指标通过率:")
    order = ["non_empty", "geo_feasible", "open_now", "eta_ok", "category", "budget",
             "assemble_budget_ok", "assemble_well_used", "assemble_refuse_unsat", "reorder_match"]
    for k in order:
        if k in agg:
            vals = agg[k]
            print(f"  {k:22s} {sum(vals)/len(vals)*100:5.1f}%  ({sum(vals)}/{len(vals)})")
    overall = [v for vals in agg.values() for v in vals]
    print("-" * 76)
    print(f"  总体通过率            {sum(overall)/len(overall)*100:5.1f}%  ({sum(overall)}/{len(overall)})")
    if degrade["total"]:
        print(f"  优雅降级(无解返回空)  {degrade['ok']/degrade['total']*100:5.1f}%  "
              f"({degrade['ok']}/{degrade['total']})  ← 5分钟/¥1 等无解约束应空,而非返回不达标门店")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())
