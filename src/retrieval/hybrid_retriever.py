"""Hybrid Retriever — one-step Qdrant retrieval with Payload pre-filtering.

Architecture: Qdrant search + Payload filter in a single query.
HNSW traversal skips vectors that don't match the filter → ~100% recall.

Replaces the FAISS funnel trap (Top-K → Python filter = 40-70% recall).
"""

import asyncio
import random

from src.models.embedder import get_embedder
from src.observability.logger import get_logger
from src.retrieval.filter_builder import build_filter
from src.retrieval.vector_store import get_vector_store
from src.config import config

logger = get_logger("hybrid_retriever")

# Mock platform APIs with simulated latency
MOCK_PLATFORMS = {
    "jd": {"name": "京东", "latency_ms": (200, 400)},
    "tb": {"name": "淘宝", "latency_ms": (300, 600)},
    "pdd": {"name": "拼多多", "latency_ms": (400, 800)},
}

# Timeout probability for mock APIs
TIMEOUT_PROBABILITY = 0.05


async def hybrid_search(
    query: str,
    entities: dict,
    top_k: int = 10,
    collection: str | None = None,
) -> dict:
    """One-step hybrid retrieval: semantic search + payload pre-filter.

    Args:
        query: User query text
        entities: Extracted entities for filtering
        top_k: Number of results to return
        collection: Qdrant collection name (default from config)

    Returns:
        {
            "results": [{"id", "score", "payload"}],
            "filter_applied": bool,
            "query": str,
            "latency_ms": float,
        }
    """
    import time
    start = time.time()

    col = collection or config["vector_db"]["collection"]
    store = get_vector_store()
    embedder = get_embedder()

    # Build query vector
    query_vector = await embedder.aembed(query)

    # Build payload filter from entities
    qdrant_filter = await build_filter(entities)
    simple_filters = _filter_to_dict(entities)

    # Log the filter being applied (with product_type + gender + price details)
    product_type = entities.get("product_type")
    category = entities.get("category")
    gender = entities.get("gender")
    price_min = entities.get("price_min")
    price_max = entities.get("price_max")
    # NOTE: 不再为日志调用 _expand_category —— 那会每次查询做无谓的品类扩展(甚至触发 embedder)。
    # build_filter 已直接用扁平 category/product_type 字段过滤,这里只记录原始实体即可。
    logger.info("filter_built",
                simple_filters=simple_filters,
                has_complex_filter=qdrant_filter is not None,
                category=category,
                product_type=product_type,
                gender=gender,
                product_type_filter_applied=product_type is not None,
                price_min=price_min,
                price_max=price_max,
                price_filter_applied=price_min is not None or price_max is not None)

    # Always run unfiltered search for comparison
    unfiltered_results = await store.search(
        collection=col, query_vector=query_vector, limit=top_k,
    )

    # Apply filters: prefer complex filter (includes price range), fallback to simple
    if qdrant_filter:
        results = await _search_with_complex_filter(
            col, query_vector, qdrant_filter, top_k
        )
    elif simple_filters:
        results = await store.search(
            collection=col, query_vector=query_vector, limit=top_k,
            filters=simple_filters,
        )
    else:
        results = unfiltered_results

    latency_ms = (time.time() - start) * 1000

    # Log unfiltered results for comparison
    logger.info("semantic_search",
                results=len(unfiltered_results),
                top_ids=[r["id"] for r in unfiltered_results[:5]])

    # Log filtered results
    logger.info("filtered_results",
                results=len(results),
                top_ids=[r["id"] for r in results[:5]])

    # Log final merged result
    logger.info("hybrid_search", query_len=len(query),
                unfiltered=len(unfiltered_results),
                filtered=len(results),
                final=len(results),
                latency_ms=round(latency_ms, 1))

    return {
        "results": results,
        "filter_applied": qdrant_filter is not None,
        "query": query,
        "latency_ms": round(latency_ms, 1),
    }


def _filter_to_dict(entities: dict) -> dict | None:
    """Convert entities to simple dict filters (for VectorStore.search)."""
    filters = {}
    if entities.get("category"):
        filters["category"] = entities["category"]
    if entities.get("product_type"):
        filters["product_type"] = entities["product_type"]
    # gender is NOT a payload field — it's encoded in category prefix ("男装/" / "女装/")
    # and embedded in the semantic query text. Do not add as hard filter.
    if entities.get("brand"):
        filters["brand"] = entities["brand"]
    return filters if filters else None


async def _search_with_complex_filter(
    collection: str,
    query_vector: list[float],
    qdrant_filter,
    limit: int,
) -> list[dict]:
    """Search with complex Qdrant Filter (range, MatchAny, etc.)."""
    store = get_vector_store()
    if hasattr(store, '_client'):
        results = await store._client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=limit,
            query_filter=qdrant_filter,
        )
        return [{"id": r.id, "score": r.score, "payload": r.payload}
                for r in results.points]
    return []


async def mock_platform_search(
    platform_id: str,
    query: str,
    entities: dict,
) -> dict:
    """Mock platform API call with simulated latency and timeout.

    Args:
        platform_id: "jd", "tb", or "pdd"
        query: Search query
        entities: Filter conditions

    Returns:
        {"platform": str, "results": list, "latency_ms": float, "error": str|None}
    """
    platform = MOCK_PLATFORMS.get(platform_id)
    if not platform:
        return {"platform": platform_id, "results": [], "latency_ms": 0,
                "error": f"unknown platform: {platform_id}"}

    # Simulate latency
    min_lat, max_lat = platform["latency_ms"]
    latency = random.uniform(min_lat / 1000, max_lat / 1000)

    # Simulate 5% timeout
    if random.random() < TIMEOUT_PROBABILITY:
        await asyncio.sleep(2.0)  # Timeout simulation
        logger.warning("platform_timeout", platform=platform_id)
        return {"platform": platform_id, "results": [], "latency_ms": 2000,
                "error": "timeout"}

    await asyncio.sleep(latency)

    # Mock results from the platform
    from src.tools.search_tool import load_products
    products = load_products()
    matched = []
    for p in products:
        if p.get("platform_id") != platform_id:
            continue
        if entities.get("category") and p["category"] != entities["category"]:
            continue
        if entities.get("price_max") and p["price"] > entities["price_max"]:
            continue
        matched.append(p)

    latency_ms = latency * 1000
    logger.info("platform_search", platform=platform_id,
                results=len(matched), latency_ms=round(latency_ms, 1))

    return {"platform": platform_id, "results": matched[:5],
            "latency_ms": round(latency_ms, 1), "error": None}


async def multi_platform_search(
    query: str,
    entities: dict,
    platforms: list[str] | None = None,
) -> list[dict]:
    """Search across multiple platforms in parallel.

    Returns list of platform results (may include timeout errors).
    """
    target_platforms = platforms or list(MOCK_PLATFORMS.keys())
    tasks = [
        mock_platform_search(pid, query, entities)
        for pid in target_platforms
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output = []
    for r in results:
        if isinstance(r, Exception):
            output.append({"error": str(r), "results": []})
        else:
            output.append(r)

    return output
