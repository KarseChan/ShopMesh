"""T2.5 Promotion Calculator tests."""

import pytest

from src.tools.promotion_calculator import (
    calculate_threshold_discount,
    calculate_step_discount,
    detect_abnormal,
    calculate_promotion,
    _load_promotions,
)


# === Type 1: 满减 ===

def test_threshold_discount_met():
    """满100减20: subtotal >= threshold."""
    result = calculate_threshold_discount(60, 2, 100, 20)
    assert result["subtotal"] == 120
    assert result["discount"] == 20
    assert result["final_price"] == 100
    assert result["promo_desc"] == "满100减20"


def test_threshold_discount_not_met():
    """满100减20: subtotal < threshold."""
    result = calculate_threshold_discount(30, 2, 100, 20)
    assert result["subtotal"] == 60
    assert result["discount"] == 0
    assert result["final_price"] == 60
    assert result["promo_desc"] is None


def test_threshold_discount_exact():
    """满100减20: subtotal == threshold."""
    result = calculate_threshold_discount(50, 2, 100, 20)
    assert result["discount"] == 20
    assert result["final_price"] == 80


# === Type 2: 阶梯折扣 ===

def test_step_discount_enough_quantity():
    """买3件打7折: quantity >= min_quantity."""
    result = calculate_step_discount(100, 3, 3, 0.7)
    assert result["subtotal"] == 300
    assert result["discount"] == 90  # 300 - 210
    assert result["final_price"] == 210
    assert result["suggest_more"] is False


def test_step_discount_not_enough():
    """买3件打7折: quantity < min_quantity → suggest more."""
    result = calculate_step_discount(100, 2, 3, 0.7)
    assert result["subtotal"] == 200
    assert result["discount"] == 0
    assert result["final_price"] == 200
    assert result["suggest_more"] is True
    assert result["suggested_quantity"] == 3
    assert "再买" in result["suggest_message"]


def test_step_discount_extra_quantity():
    """买3件打7折: quantity > min_quantity, only first 3 get discount."""
    result = calculate_step_discount(100, 5, 3, 0.7)
    # 3 discounted (210) + 2 full price (200) = 410
    assert result["final_price"] == 410
    assert result["discount"] == 90


# === Type 3: 异常检测 ===

def test_detect_shipping_trap():
    """运费陷阱: cheap item + high shipping."""
    result = detect_abnormal(1, 1, shipping_fee=50)
    assert result["is_abnormal"] is True
    assert len(result["warnings"]) > 0
    assert result["abnormal_type"] == "shipping_trap"


def test_detect_price_anomaly():
    """价格异常: much lower than historical."""
    result = detect_abnormal(5, 1, historical_price=199)
    assert result["is_abnormal"] is True
    assert result["abnormal_type"] == "price_anomaly"


def test_detect_normal():
    """Normal price, no anomaly."""
    result = detect_abnormal(50, 2, shipping_fee=5)
    assert result["is_abnormal"] is False


def test_detect_suspiciously_cheap():
    """Unit price < 1 yuan is suspicious."""
    result = detect_abnormal(0.5, 1)
    assert result["is_abnormal"] is True


# === Integration: calculate_promotion ===

def test_load_promotions():
    promotions = _load_promotions()
    assert "promo_01" in promotions
    assert "promo_03" in promotions
    assert "promo_05" in promotions


def test_calculate_promotion_threshold():
    """promo_01: 满100减20."""
    product = {"product_id": "prod_001", "price": 599, "promotion_id": "promo_01"}
    result = calculate_promotion(product, quantity=1)
    assert result["subtotal"] == 599
    assert result["discount"] == 20
    assert result["final_price"] == 579
    assert result["promo_type"] == "threshold_discount"


def test_calculate_promotion_step():
    """promo_03: 买3件打7折."""
    product = {"product_id": "prod_005", "price": 15, "promotion_id": "promo_03"}
    result = calculate_promotion(product, quantity=2)
    assert result["suggest_more"] is True
    assert "再买" in result["suggest_message"]

    result3 = calculate_promotion(product, quantity=3)
    assert result3["discount"] > 0
    assert result3["suggest_more"] is False


def test_calculate_promotion_abnormal_shipping():
    """promo_05: 商品1元运费50元."""
    product = {"product_id": "prod_012", "price": 5, "promotion_id": "promo_05"}
    result = calculate_promotion(product, quantity=1)
    assert result["is_abnormal"] is True
    assert len(result["warnings"]) > 0


def test_calculate_promotion_no_promo():
    """Product without promotion."""
    product = {"product_id": "prod_006", "price": 4, "promotion_id": None}
    result = calculate_promotion(product, quantity=1)
    assert result["discount"] == 0
    assert result["final_price"] == 4
    assert result["promo_type"] is None


def test_calculate_promotion_with_historical():
    """Historical price triggers anomaly detection."""
    product = {"product_id": "test", "price": 5, "promotion_id": None}
    result = calculate_promotion(product, quantity=1, historical_price=199)
    assert result["is_abnormal"] is True
