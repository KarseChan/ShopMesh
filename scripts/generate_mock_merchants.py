"""Generate mock merchants for instant-delivery (秒送) retrieval.

Produces data/mock_merchants.json — a flat list mirroring the products mock
pattern. Merchants are scattered around a demo user location (src/retrieval/geo.py
DEMO_USER_LOCATION) so the geo/open/eta constraint push-down can be verified
命令行 without a real map API.

By design the 奶茶 (bubble-tea) category includes explicit NEGATIVE cases so a
verify run proves each hard filter actually prunes:
  - 太远  (distance > delivery_radius)      → geo/haversine 过滤
  - 已打烊 (open_hour/close_hour 排除当前时) → 营业中过滤
  - 太慢  (delivery_minutes > 30)            → 时效过滤
plus several stores that satisfy all of "附近奶茶店 30 分钟送到".

Usage:
    python scripts/generate_mock_merchants.py
    python scripts/generate_mock_merchants.py --output data/mock_merchants.json
"""

import argparse
import json
import random
from pathlib import Path

from src.retrieval.geo import DEMO_USER_LOCATION, haversine_km

_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "mock_merchants.json"

_CITY = "深圳"
_SEED = 20260806

# ~0.009 度 ≈ 1km(纬度). 用它把门店按"距 demo 用户的公里数"放置。
_KM_PER_DEG = 1 / 111.0


def _offset(center: dict, north_km: float, east_km: float) -> tuple[float, float]:
    """Return (lat, lon) offset from center by given km (north/east)."""
    import math
    lat = center["lat"] + north_km * _KM_PER_DEG
    # 经度每度对应距离随纬度收缩
    lon = center["lon"] + east_km * _KM_PER_DEG / math.cos(math.radians(center["lat"]))
    return round(lat, 6), round(lon, 6)


# 奶茶品牌 + 招牌,用于生成名字/embedding_text/tags
_TEA_BRANDS = ["喜茶", "奈雪的茶", "茶百道", "蜜雪冰城", "coco都可", "古茗", "沪上阿姨", "书亦烧仙草"]
_TEA_TAGS = ["网红", "手打柠檬茶", "多肉葡萄", "芝士奶盖", "少糖可选", "招牌波波"]

_OTHER = [
    ("快餐", ["麦当劳", "肯德基", "华莱士", "老乡鸡"], ["汉堡", "炸鸡", "米饭套餐"]),
    ("超市便利", ["美宜佳", "全家", "钱大妈"], ["日用", "零食", "生鲜"]),
    ("药店", ["海王星辰", "大参林"], ["感冒药", "口罩", "创可贴"]),
]


def _make_tea_store(idx: int, dist_km: float, bearing_deg: float, *,
                    open_hour: int, close_hour: int, eta: int,
                    radius: float, rng: random.Random) -> dict:
    import math
    north = dist_km * math.cos(math.radians(bearing_deg))
    east = dist_km * math.sin(math.radians(bearing_deg))
    lat, lon = _offset(DEMO_USER_LOCATION, north, east)
    brand = rng.choice(_TEA_BRANDS)
    tags = rng.sample(_TEA_TAGS, k=3)
    name = f"{brand}(南山{idx}号店)"
    return {
        "merchant_id": f"m_tea_{idx:03d}",
        "name": name,
        "city": _CITY,
        "latitude": lat,
        "longitude": lon,
        "category": "奶茶",
        "rating": round(rng.uniform(4.0, 4.9), 1),
        "avg_price": round(rng.uniform(12, 28), 1),
        "delivery_fee": round(rng.choice([0, 2, 3, 5]), 1),
        "delivery_minutes": eta,
        "delivery_radius_km": radius,
        "open_hour": open_hour,
        "close_hour": close_hour,
        "open_hours": "24小时" if (open_hour == 0 and close_hour == 24) else f"{open_hour:02d}:00-{close_hour:02d}:00",
        "tags": tags,
        "image_url": f"https://picsum.photos/seed/{brand}{idx}/400/400",
        "embedding_text": f"奶茶 饮品 {brand} " + " ".join(tags),
    }


def generate() -> list[dict]:
    rng = random.Random(_SEED)
    merchants: list[dict] = []
    idx = 1

    # ---- 奶茶:满足约束的"正例"(近 + ETA≤30) ----
    # 正例设为 24h 营业(open 0 / close 24),使 demo 在任意时刻都能召回(秒送常见 24h 店);
    # "营业中"过滤仍由下方**显式打烊**的负例证明,不依赖正例的时段。
    positives = [
        # dist_km, bearing, eta, radius
        (0.6, 30, 18, 3.0),
        (0.9, 120, 22, 3.0),
        (1.2, 200, 25, 3.0),
        (1.6, 300, 28, 3.0),
        (2.2, 80, 30, 5.0),   # 边界:ETA 正好 30、距离 2.2km 在 5km 半径内
        (0.4, 10, 15, 2.0),
    ]
    for dist, bearing, eta, radius in positives:
        merchants.append(_make_tea_store(
            idx, dist, bearing, open_hour=0, close_hour=24,
            eta=eta, radius=radius, rng=rng))
        idx += 1

    # ---- 奶茶:反例 —— 太远(超出配送半径);24h 营业,确保被 geo 过滤而非被打烊掩盖 ----
    for dist, bearing, radius in [(6.0, 45, 3.0), (8.5, 160, 3.0), (5.5, 250, 3.0)]:
        merchants.append(_make_tea_store(
            idx, dist, bearing, open_hour=0, close_hour=24,
            eta=25, radius=radius, rng=rng))
        idx += 1

    # ---- 奶茶:反例 —— 已打烊(营业时段不含常见白天时间) ----
    # 夜宵店 20:00-04:00 用 close_hour=28(次日4点)表示;白天验证时它应被排除。
    merchants.append(_make_tea_store(
        idx, 0.7, 90, open_hour=20, close_hour=28, eta=20, radius=3.0, rng=rng))
    idx += 1
    # 尚未开门(15:00 才开)
    merchants.append(_make_tea_store(
        idx, 0.8, 270, open_hour=15, close_hour=23, eta=20, radius=3.0, rng=rng))
    idx += 1

    # ---- 奶茶:反例 —— 太慢(ETA > 30);24h 营业,确保被时效过滤而非被打烊掩盖 ----
    for dist, eta in [(1.0, 45), (1.4, 55)]:
        merchants.append(_make_tea_store(
            idx, dist, 150, open_hour=0, close_hour=24,
            eta=eta, radius=3.0, rng=rng))
        idx += 1

    # ---- 其它品类(近、营业中):证明 category 过滤把它们排除在"奶茶"外 ----
    for category, brands, dishes in _OTHER:
        for _ in range(3):
            dist = rng.uniform(0.5, 2.5)
            bearing = rng.uniform(0, 360)
            import math
            north = dist * math.cos(math.radians(bearing))
            east = dist * math.sin(math.radians(bearing))
            lat, lon = _offset(DEMO_USER_LOCATION, north, east)
            brand = rng.choice(brands)
            tags = rng.sample(dishes, k=min(2, len(dishes)))
            merchants.append({
                "merchant_id": f"m_{category}_{idx:03d}",
                "name": f"{brand}(南山{idx}号店)",
                "city": _CITY,
                "latitude": lat,
                "longitude": lon,
                "category": category,
                "rating": round(rng.uniform(4.0, 4.9), 1),
                "avg_price": round(rng.uniform(15, 60), 1),
                "delivery_fee": round(rng.choice([0, 3, 5, 6]), 1),
                "delivery_minutes": rng.choice([20, 25, 30, 35]),
                "delivery_radius_km": rng.choice([3.0, 5.0]),
                "open_hour": 0,
                "close_hour": 24,
                "open_hours": "24小时",
                "tags": tags,
                "image_url": f"https://picsum.photos/seed/{brand}{idx}/400/400",
                "embedding_text": f"{category} {brand} " + " ".join(tags),
            })
            idx += 1

    # 回填精确距离(便于人工核对;检索路径自己也会算)
    for m in merchants:
        m["_distance_km_from_demo"] = round(
            haversine_km(DEMO_USER_LOCATION["lat"], DEMO_USER_LOCATION["lon"],
                         m["latitude"], m["longitude"]), 3)
    return merchants


def main():
    parser = argparse.ArgumentParser(description="Generate mock merchants")
    parser.add_argument("--output", default=str(_DEFAULT_OUTPUT))
    args = parser.parse_args()

    merchants = generate()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(merchants, f, ensure_ascii=False, indent=2)

    tea = [m for m in merchants if m["category"] == "奶茶"]
    print(f"Generated {len(merchants)} merchants ({len(tea)} 奶茶) → {out}")
    print(f"Demo user location: {DEMO_USER_LOCATION}")


if __name__ == "__main__":
    main()
