"""Generate mock dishes (菜品) per merchant for the 秒送 order 闭环 (P2).

Dishes are the orderable items. They mirror the product schema so the existing
cart/order/payment machinery resolves them by id verbatim (item_type='dish',
merchant_id links to the store). Reads data/mock_merchants.json, writes
data/mock_dishes.json (a flat list, like mock_products_5k.json).

Only categories that make sense to 点单 get menus (奶茶/快餐/超市便利/药店).

Usage:
    python scripts/generate_mock_dishes.py
"""

import argparse
import json
import random
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MERCHANTS = _ROOT / "data" / "mock_merchants.json"
_DEFAULT_OUTPUT = _ROOT / "data" / "mock_dishes.json"
_SEED = 20260807

# 每个品类的菜品模板: (菜名, 价格区间)
_MENU_TEMPLATES = {
    "奶茶": [
        ("招牌珍珠奶茶", (12, 18)),
        ("多肉葡萄", (18, 26)),
        ("芝士奶盖乌龙", (15, 22)),
        ("手打柠檬茶", (13, 19)),
        ("烧仙草", (14, 20)),
        ("波波奶茶", (10, 16)),
    ],
    "快餐": [
        ("香辣鸡腿堡套餐", (22, 32)),
        ("黄金炸鸡(6块)", (18, 28)),
        ("照烧鸡排饭", (20, 30)),
        ("薯条(大)", (9, 14)),
        ("可乐(中)", (6, 9)),
    ],
    "超市便利": [
        ("矿泉水 550ml", (2, 4)),
        ("薯片", (6, 12)),
        ("方便面", (4, 8)),
        ("鲜牛奶 1L", (12, 18)),
    ],
    "药店": [
        ("感冒灵颗粒", (12, 20)),
        ("医用口罩(10只)", (8, 15)),
        ("创可贴", (5, 10)),
    ],
}


def generate() -> list[dict]:
    with open(_MERCHANTS, encoding="utf-8") as f:
        merchants = json.load(f)
    rng = random.Random(_SEED)

    dishes: list[dict] = []
    for m in merchants:
        cat = m["category"]
        template = _MENU_TEMPLATES.get(cat)
        if not template:
            continue
        mid = m["merchant_id"]
        # 每店取模板的一个子集(3~5 道)
        k = min(len(template), rng.randint(3, 5))
        chosen = rng.sample(template, k=k)
        for i, (dish_name, (lo, hi)) in enumerate(chosen, 1):
            price = round(rng.uniform(lo, hi), 1)
            dishes.append({
                "product_id": f"{mid}_d{i}",
                "name": f"{dish_name}",
                "category": cat,
                "product_type": dish_name,
                "brand": m["name"],
                "price": price,
                "stock": rng.randint(20, 200),
                "platform_id": "instant",
                "merchant_id": mid,
                "item_type": "dish",
                "rating": m.get("rating", 4.5),
                "features": m.get("tags", [])[:3],
                "embedding_text": f"{cat} {dish_name} {m['name']}",
                "image_url": f"https://picsum.photos/seed/{mid}d{i}/400/400",
            })
    return dishes


def main():
    parser = argparse.ArgumentParser(description="Generate mock dishes")
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT))
    args = parser.parse_args()

    dishes = generate()
    out = Path(args.output)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dishes, f, ensure_ascii=False, indent=2)

    by_merchant = {}
    for d in dishes:
        by_merchant.setdefault(d["merchant_id"], 0)
        by_merchant[d["merchant_id"]] += 1
    print(f"Generated {len(dishes)} dishes across {len(by_merchant)} merchants → {out}")


if __name__ == "__main__":
    main()
