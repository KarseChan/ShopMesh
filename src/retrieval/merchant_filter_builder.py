"""Merchant Filter Builder — push instant-delivery constraints INTO Qdrant.

Same手法 as the product scenario-whitelist push-down (filter_builder.py): compose
a Qdrant Filter so HNSW only traverses feasible merchants, instead of retrieving
a top-K and emptying it with a post-filter.

Pushed down (hard, in Qdrant):
  - GeoRadius(user, coarse_radius): prune merchants clearly out of range. Coarse
    because each store has its OWN delivery_radius_km — a single GeoRadius can't
    express a per-store radius, so we push a generous ceiling and do the exact
    `haversine ≤ store.delivery_radius_km` check as a post-filter (merchant_search).
  - open_hour ≤ now_hour < close_hour: 营业中. Real time push-down (Range on ints),
    NOT a static baked `open_now` flag — recomputed per query. close_hour may exceed
    24 (e.g. 28 = 次日04:00) to mark 夜宵 stores; a daytime query correctly excludes
    them. The post-midnight tail (matching a 20-28 store at 02:00) needs now_hour+24
    and is a known 1a limitation — daytime verification doesn't hit it.
  - delivery_minutes ≤ max_delivery_minutes: 时效 (only when the user gave one).
  - avg_price ≤ budget: 预算 (optional).
  - category == merchant_category: 品类 (e.g. 奶茶), when given.
"""

from qdrant_client.models import (
    FieldCondition,
    Filter,
    GeoPoint,
    GeoRadius,
    MatchValue,
    Range,
)

# Coarse geo push-down ceiling. Larger than any single store's delivery_radius_km
# so HNSW never wrongly prunes a reachable store; the exact per-store radius check
# runs as a post-filter after retrieval.
COARSE_RADIUS_KM = 6.0


def build_merchant_filter(
    location: dict,
    *,
    now_hour: int,
    merchant_category: str | None = None,
    max_delivery_minutes: int | None = None,
    budget: float | None = None,
    coarse_radius_km: float = COARSE_RADIUS_KM,
) -> Filter:
    """Compose a Qdrant Filter for instant-delivery retrieval.

    Args:
        location: {"lat": float, "lon": float} — the user's position.
        now_hour: current hour of day (0-23), used for the 营业中 push-down.
        merchant_category: e.g. "奶茶" (hard category filter) or None.
        max_delivery_minutes: 时效上限 (ETA), or None to not constrain.
        budget: avg_price 上限, or None.
        coarse_radius_km: coarse geo push-down ceiling.

    Returns:
        A Qdrant Filter (always has at least the geo + open-now conditions).
    """
    conditions = [
        # 就近:粗半径,只召回可能可达的门店
        FieldCondition(
            key="location",
            geo_radius=GeoRadius(
                center=GeoPoint(lat=location["lat"], lon=location["lon"]),
                radius=coarse_radius_km * 1000,  # Qdrant geo radius is in meters
            ),
        ),
        # 营业中:open_hour ≤ now < close_hour
        FieldCondition(key="open_hour", range=Range(lte=now_hour)),
        FieldCondition(key="close_hour", range=Range(gt=now_hour)),
    ]

    if merchant_category:
        conditions.append(FieldCondition(
            key="category", match=MatchValue(value=merchant_category)))

    if max_delivery_minutes is not None:
        conditions.append(FieldCondition(
            key="delivery_minutes", range=Range(lte=float(max_delivery_minutes))))

    if budget is not None:
        conditions.append(FieldCondition(
            key="avg_price", range=Range(lte=float(budget))))

    return Filter(must=conditions)
