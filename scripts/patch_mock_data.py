"""Patch mock_data.json to add missing fields.

Adds:
1. `rating` (float 3.0-5.0) to all products
2. `promotions` array with realistic promotion rules
3. `promotion_id` references on ~30% of products

Usage:
    python scripts/patch_mock_data.py
    python scripts/patch_mock_data.py --dry-run  # preview without writing
"""

import json
import random
import sys
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "mock_data.json"

# Promotion templates — covers all 3 types from promotion_calculator.py
PROMOTION_TEMPLATES = [
    # Type 1: threshold_discount (满减)
    {"type": "threshold_discount", "threshold": 200, "reduce": 30, "desc": "满200减30"},
    {"type": "threshold_discount", "threshold": 300, "reduce": 50, "desc": "满300减50"},
    {"type": "threshold_discount", "threshold": 500, "reduce": 80, "desc": "满500减80"},
    {"type": "threshold_discount", "threshold": 100, "reduce": 10, "desc": "满100减10"},
    {"type": "threshold_discount", "threshold": 150, "reduce": 20, "desc": "满150减20"},
    # Type 2: step_discount (阶梯折扣)
    {"type": "step_discount", "min_quantity": 2, "discount_rate": 0.85, "desc": "买2件打85折"},
    {"type": "step_discount", "min_quantity": 3, "discount_rate": 0.8, "desc": "买3件打8折"},
    {"type": "step_discount", "min_quantity": 2, "discount_rate": 0.9, "desc": "买2件打9折"},
]


def generate_rating(base_reputation: float) -> float:
    """Generate a realistic rating (3.0-5.0) correlated with reputation."""
    # Higher reputation → higher rating, with some noise
    base = 3.0 + base_reputation * 2.0  # reputation 0.66 → ~4.3
    noise = random.uniform(-0.3, 0.3)
    return round(max(3.0, min(5.0, base + noise)), 1)


def main():
    dry_run = "--dry-run" in sys.argv

    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)

    products = data.get("products", [])
    if not products:
        print("ERROR: No products found in mock_data.json")
        return

    random.seed(42)  # reproducible

    # 1. Add rating to all products
    for p in products:
        if "rating" not in p:
            p["rating"] = generate_rating(p.get("reputation", 0.5))

    # 2. Build promotions list
    promotions = []
    for i, tmpl in enumerate(PROMOTION_TEMPLATES, 1):
        promo = {
            "promotion_id": f"promo_{i:03d}",
            "type": tmpl["type"],
            "description": tmpl["desc"],
        }
        # Add type-specific fields
        if tmpl["type"] == "threshold_discount":
            promo["threshold"] = tmpl["threshold"]
            promo["reduce"] = tmpl["reduce"]
        elif tmpl["type"] == "step_discount":
            promo["min_quantity"] = tmpl["min_quantity"]
            promo["discount_rate"] = tmpl["discount_rate"]
        promotions.append(promo)

    # 3. Assign promotion_id to ~30% of products
    promo_ids = [p["promotion_id"] for p in promotions]
    assigned_count = 0
    for p in products:
        if random.random() < 0.3:
            p["promotion_id"] = random.choice(promo_ids)
            assigned_count += 1

    data["promotions"] = promotions

    print(f"Products: {len(products)}")
    print(f"  with rating added: {len(products)}")
    print(f"  with promotion_id assigned: {assigned_count}")
    print(f"Promotions: {len(promotions)}")
    for promo in promotions:
        print(f"  {promo['promotion_id']}: {promo['description']}")

    if dry_run:
        print("\n[dry-run] No changes written.")
    else:
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\nWritten to {DATA_PATH}")


if __name__ == "__main__":
    main()
