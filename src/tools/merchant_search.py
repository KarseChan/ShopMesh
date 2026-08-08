"""Nearby Merchant Search Tool — 秒送版就近检索 for the instant-order agent.

Mirrors product_search's shape (hybrid retrieve → rank → slim) but for merchants:
constraints (geo/营业中/时效/预算/品类) are pushed INTO Qdrant, an exact per-store
haversine radius check runs as a post-filter, and results are ranked 时效优先.

This is a *deterministic* tool — no LLM. The agent (later slice) only decides WHEN
to call it and with what parsed constraints; the constraint solving is all rules,
per the plan's recsys/agent boundary.
"""

from datetime import datetime

from src.agents.merchant_ranker import rank_merchants
from src.config import config
from src.models.embedder import get_embedder
from src.observability.logger import get_logger
from src.retrieval.geo import haversine_km
from src.retrieval.merchant_filter_builder import build_merchant_filter
from src.retrieval.vector_store import get_vector_store
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("merchant_search")


def _merchant_collection() -> str:
    return config["vector_db"].get("merchant_collection", "merchants")


def _slim(m: dict) -> dict:
    """Fields the agent / frontend需要 for a store card."""
    return {
        "merchant_id": m.get("merchant_id", ""),
        "name": m.get("name", ""),
        "category": m.get("category", ""),
        "rating": m.get("rating"),
        "avg_price": m.get("avg_price"),
        "delivery_fee": m.get("delivery_fee"),
        "delivery_minutes": m.get("delivery_minutes"),
        "distance_km": m.get("distance_km"),
        "open_hours": m.get("open_hours", ""),
        "tags": m.get("tags", []),
        "image_url": m.get("image_url", ""),
        "rank_score": m.get("rank_score"),
        "rank_reason_text": m.get("rank_reason_text", ""),
    }


async def nearby_merchant_search(
    location: dict,
    semantic_query: str = "",
    merchant_category: str | None = None,
    max_delivery_minutes: int | None = None,
    budget: float | None = None,
    now_hour: int | None = None,
    top_k: int = 30,
    max_results: int = 10,
    rank_profile: str = "instant_delivery",
) -> dict:
    """就近约束检索:geo/营业中/时效/预算 下推 + 精确半径过滤 + 时效优先排序.

    Args:
        location: {"lat", "lon"} 用户位置.
        semantic_query: 语义检索文本(口味/品类描述),用于向量召回 + 排序.
        merchant_category: 硬品类过滤,如 "奶茶".
        max_delivery_minutes: 时效上限(ETA);None 不限.
        budget: 人均价上限;None 不限.
        now_hour: 当前小时(0-23),默认取系统时间;显式传入便于确定性验证.
        top_k: 向量召回数(下推过滤后).
        max_results: 排序后返回上限.
        rank_profile: 排序 profile(默认 instant_delivery 时效优先).

    Returns:
        {"results": [slim...], "_full": [...], "total": int, "retrieved": int,
         "filtered_by_radius": int, "now_hour": int, "constraints": {...}}
    """
    import time
    start = time.time()

    if now_hour is None:
        now_hour = datetime.now().hour

    collection = _merchant_collection()
    store = get_vector_store()

    qdrant_filter = build_merchant_filter(
        location,
        now_hour=now_hour,
        merchant_category=merchant_category,
        max_delivery_minutes=max_delivery_minutes,
        budget=budget,
    )

    # 向量召回 + payload 下推过滤(单步,HNSW 只遍历可行门店).
    query_vector = await get_embedder().aembed(semantic_query or (merchant_category or "外卖"))
    raw = await store._client.query_points(
        collection_name=collection,
        query=query_vector,
        limit=top_k,
        query_filter=qdrant_filter,
    )
    retrieved = [{**p.payload, "_score": p.score} for p in raw.points]

    # 精确半径过滤:每店 delivery_radius_km 不同,GeoRadius 只是粗筛.
    within = []
    for m in retrieved:
        dist = haversine_km(location["lat"], location["lon"],
                            m["latitude"], m["longitude"])
        if dist <= float(m.get("delivery_radius_km", 3.0)):
            m["distance_km"] = round(dist, 3)
            within.append(m)

    ranked = rank_merchants(within, profile=rank_profile)[:max_results]

    latency_ms = round((time.time() - start) * 1000, 1)
    logger.info("nearby_merchant_search_done",
                retrieved=len(retrieved), within_radius=len(within),
                returned=len(ranked), now_hour=now_hour,
                category=merchant_category, max_eta=max_delivery_minutes,
                latency_ms=latency_ms)

    return {
        "results": [_slim(m) for m in ranked],
        "_full": ranked,
        "total": len(ranked),
        "retrieved": len(retrieved),
        "filtered_by_radius": len(retrieved) - len(within),
        "now_hour": now_hour,
        "latency_ms": latency_ms,
        "constraints": {
            "merchant_category": merchant_category,
            "max_delivery_minutes": max_delivery_minutes,
            "budget": budget,
        },
    }


tool_registry.register(ToolDef(
    name="nearby_merchant_search",
    description="秒送就近门店检索:传入用户位置和约束(品类/时效/预算),"
                "在营业中、可送达、满足时效的门店里做向量召回+时效优先排序。"
                "约束求解是确定性的,不调用 LLM。",
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "object",
                "description": "用户位置 {lat, lon}",
            },
            "semantic_query": {
                "type": "string",
                "description": "语义检索文本(口味/品类描述,如'微辣 招牌奶茶')",
            },
            "merchant_category": {
                "type": "string",
                "description": "门店品类硬过滤,如 '奶茶' / '快餐'",
            },
            "max_delivery_minutes": {
                "type": "integer",
                "description": "时效上限(分钟),用户说'30分钟到'时传 30",
            },
            "budget": {
                "type": "number",
                "description": "人均价上限,可选",
            },
        },
        "required": ["location"],
    },
    return_type="dict",
    func=nearby_merchant_search,
))
