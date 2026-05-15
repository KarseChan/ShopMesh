"""Promotion Calculator — stateless promotion calculation tool.

3 promotion types:
1. threshold_discount (满减): spend X get Y off — demonstrates tool call chain
2. step_discount (阶梯折扣): buy N get X% off — triggers clarification "wanna buy more?"
3. abnormal_shipping (运费陷阱): cheap item + high shipping — triggers safety guard

Architecture: stateless node, input = structured cart payload, output = final price.
Production: swap with real settlement API, no Agent core changes needed.
"""

import json
from pathlib import Path

from src.observability.logger import get_logger

logger = get_logger("promotion_calculator")

_PROMO_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "mock_data.json"

_promotions_cache: dict[str, dict] | None = None


def _load_promotions() -> dict[str, dict]:
    """Load promotions from mock_data.json, keyed by promotion_id."""
    global _promotions_cache
    if _promotions_cache is not None:
        return _promotions_cache

    with open(_PROMO_PATH, encoding="utf-8") as f:
        data = json.load(f)

    _promotions_cache = {p["promotion_id"]: p for p in data.get("promotions", [])}
    return _promotions_cache


def calculate_threshold_discount(
    unit_price: float, quantity: int, threshold: float, reduce: float
) -> dict:
    """Type 1: 满减 — spend threshold get reduce off.

    Returns:
        {"subtotal": float, "discount": float, "final_price": float, "promo_desc": str}
    """
    subtotal = unit_price * quantity
    discount = 0.0
    if subtotal >= threshold:
        discount = reduce

    final = max(0, subtotal - discount)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "final_price": final,
        "promo_desc": f"满{threshold}减{reduce}" if discount > 0 else None,
    }


def calculate_step_discount(
    unit_price: float, quantity: int, min_quantity: int, discount_rate: float
) -> dict:
    """Type 2: 阶梯折扣 — buy min_quantity or more to get discount.

    Returns:
        {"subtotal": float, "discount": float, "final_price": float,
         "promo_desc": str, "suggest_more": bool, "suggested_quantity": int}
    """
    subtotal = unit_price * quantity

    if quantity >= min_quantity:
        # Apply discount
        discounted_total = unit_price * min_quantity * discount_rate + unit_price * (quantity - min_quantity)
        discount = subtotal - discounted_total
        return {
            "subtotal": subtotal,
            "discount": round(discount, 2),
            "final_price": round(discounted_total, 2),
            "promo_desc": f"买{min_quantity}件打{int(discount_rate * 10)}折",
            "suggest_more": False,
            "suggested_quantity": quantity,
        }
    else:
        # Not enough quantity — suggest buying more
        needed = min_quantity - quantity
        saving = unit_price * min_quantity * (1 - discount_rate)
        return {
            "subtotal": subtotal,
            "discount": 0,
            "final_price": subtotal,
            "promo_desc": None,
            "suggest_more": True,
            "suggested_quantity": min_quantity,
            "suggest_message": f"再买{needed}件可享{int(discount_rate * 10)}折，省{saving:.0f}元",
        }


def detect_abnormal(
    unit_price: float, quantity: int, shipping_fee: float = 0,
    historical_price: float | None = None,
) -> dict:
    """Type 3: 异常检测 — detect price anomalies and shipping traps.

    Returns:
        {"is_abnormal": bool, "warnings": list[str], "abnormal_type": str|None}
    """
    warnings = []
    abnormal_type = None

    # Check: shipping fee trap (cheap item + high shipping)
    total = unit_price * quantity
    if shipping_fee > 0 and shipping_fee > total * 0.5:
        warnings.append(f"运费{shipping_fee}元，超过商品价格的50%")
        abnormal_type = "shipping_trap"

    # Check: suspiciously low price (historical price significantly higher)
    if historical_price and historical_price > 0:
        ratio = unit_price / historical_price
        if ratio < 0.1:  # Less than 10% of historical price
            warnings.append(
                f"当前价格{unit_price}元仅为历史价{historical_price}元的{ratio*100:.0f}%，可能存在异常"
            )
            abnormal_type = "price_anomaly"

    # Check: unit price < 1 yuan (suspiciously cheap)
    if unit_price < 1 and unit_price > 0:
        warnings.append(f"单价{unit_price}元异常低，可能存在隐藏费用")
        abnormal_type = "price_anomaly"

    return {
        "is_abnormal": len(warnings) > 0,
        "warnings": warnings,
        "abnormal_type": abnormal_type,
    }


def calculate_promotion(
    product: dict,
    quantity: int = 1,
    historical_price: float | None = None,
) -> dict:
    """Main entry: calculate final price for a product with its promotion.

    Args:
        product: Product dict (must have price, promotion_id)
        quantity: Number of items
        historical_price: Reference price for anomaly detection

    Returns:
        {
            "product_id": str,
            "unit_price": float,
            "quantity": int,
            "subtotal": float,
            "discount": float,
            "final_price": float,
            "promo_type": str|None,
            "promo_desc": str|None,
            "suggest_more": bool,
            "suggest_message": str|None,
            "is_abnormal": bool,
            "warnings": list[str],
        }
    """
    promotions = _load_promotions()

    unit_price = product.get("price", 0)
    promo_id = product.get("promotion_id")
    promo = promotions.get(promo_id) if promo_id else None

    result = {
        "product_id": product.get("product_id", ""),
        "unit_price": unit_price,
        "quantity": quantity,
        "subtotal": unit_price * quantity,
        "discount": 0,
        "final_price": unit_price * quantity,
        "promo_type": None,
        "promo_desc": None,
        "suggest_more": False,
        "suggest_message": None,
        "is_abnormal": False,
        "warnings": [],
    }

    # Run anomaly detection even without promotion (historical price check)
    if historical_price and not result["is_abnormal"]:
        anomaly = detect_abnormal(unit_price, quantity, 0, historical_price)
        if anomaly["is_abnormal"]:
            result["is_abnormal"] = True
            result["warnings"].extend(anomaly["warnings"])

    if not promo:
        return result

    promo_type = promo["type"]
    logic = promo.get("logic", {})
    result["promo_type"] = promo_type

    if promo_type == "threshold_discount":
        calc = calculate_threshold_discount(
            unit_price, quantity,
            logic.get("threshold", 0), logic.get("reduce", 0)
        )
        result.update(calc)

    elif promo_type == "step_discount":
        calc = calculate_step_discount(
            unit_price, quantity,
            logic.get("min_quantity", 1), logic.get("discount_rate", 1.0)
        )
        result.update(calc)

    elif promo_type == "abnormal_shipping":
        shipping = logic.get("shipping_fee", 0)
        # Calculate with shipping
        subtotal = unit_price * quantity
        result["final_price"] = subtotal + shipping
        result["promo_desc"] = f"运费{shipping}元"

        # Run anomaly detection
        anomaly = detect_abnormal(unit_price, quantity, shipping, historical_price)
        result["is_abnormal"] = anomaly["is_abnormal"]
        result["warnings"] = anomaly["warnings"]

    logger.info("promotion_calculated",
                product_id=result["product_id"],
                promo_type=promo_type,
                final_price=result["final_price"],
                is_abnormal=result["is_abnormal"])

    return result
