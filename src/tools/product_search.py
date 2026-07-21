"""Product Search Tool — one-stop retrieval for ReAct Agent.

Internal flow:
1. hybrid_search (Qdrant vector + payload pre-filter)
2. rank (multi-objective weighted fusion)

Agent calls this single tool instead of managing low-level retrieval details.
"""

from src.agents.ranker import rank
from src.observability.logger import get_logger
from src.retrieval.hybrid_retriever import hybrid_search
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("product_search_tool")


async def product_search(
    entities: dict,
    semantic_query: str,
    top_k: int = 10,
    max_results: int = 10,
    memory_signals: dict | None = None,
) -> dict:
    """One-stop product retrieval: hybrid search + multi-objective ranking.

    Args:
        entities: Structured entities (category, brand, price_max, scenario, etc.)
        semantic_query: Semantic search text (keywords/description from user need)
        top_k: Number of results to retrieve from vector search
        max_results: Max results to return (caps ranked output, saves LLM tokens)

    Returns:
        {"results": [...], "total": int, "filter_applied": bool, "latency_ms": float}
    """
    # Step 1: Hybrid retrieval (vector + payload pre-filter)
    search_result = await hybrid_search(semantic_query, entities, top_k=top_k)
    results = search_result["results"]

    # Step 2: Extract search scores and products
    search_scores = [r.get("score", 0.5) for r in results]
    products = [r.get("payload", r) for r in results]

    # Attach product_id from search result id
    for i, r in enumerate(results):
        if "product_id" not in products[i]:
            products[i]["product_id"] = r.get("id", "")

    # Step 3: Multi-objective ranking (with optional cross-encoder reranker)
    ranked = await rank(products, search_scores=search_scores, entities=entities,
                        memory_signals=memory_signals, original_query=semantic_query)

    # Step 4: Cap results to save LLM tokens
    ranked = ranked[:max_results]

    # Step 5: Split exact vs supplemental matches
    product_type = entities.get("product_type")
    exact_ids, supplemental_ids = _split_matches(ranked, product_type)

    logger.info("product_search_done",
                total=len(ranked),
                exact=len(exact_ids),
                supplemental=len(supplemental_ids),
                filter_applied=search_result["filter_applied"],
                latency_ms=search_result["latency_ms"])

    # Step 6: Build slim results for Agent prompt + full results for result store
    slim_results = [_slim_product(p) for p in ranked]

    return {
        "results": slim_results,
        "_full_products": ranked,
        "total": len(ranked),
        "exact": len(exact_ids),
        "supplemental": len(supplemental_ids),
        "exact_product_ids": exact_ids,
        "supplemental_product_ids": supplemental_ids,
        "display_product_ids": exact_ids,
        "filter_applied": search_result["filter_applied"],
        "latency_ms": search_result["latency_ms"],
    }


def _slim_product(p: dict) -> dict:
    """Extract only fields the LLM needs for recommendation decisions."""
    return {
        "product_id": p.get("product_id", ""),
        "name": p.get("name", ""),
        "price": p.get("price"),
        "brand": p.get("brand", ""),
        "features": p.get("features", [])[:3],
        "rating": p.get("rating"),
        "rank_score": p.get("rank_score"),
        "rank_reason_text": p.get("rank_reason_text", ""),
    }


def _split_matches(ranked: list[dict], product_type: str | None) -> tuple[list[str], list[str]]:
    """Split results into exact matches (product_type matches) and supplemental."""
    if not product_type:
        # No product_type constraint → all are exact
        return [p.get("product_id", "") for p in ranked], []

    exact, supplemental = [], []
    for p in ranked:
        pid = p.get("product_id", "")
        pt_score = p.get("rank_reasons", {}).get("product_type_match", 0)
        if pt_score >= 0.7:
            exact.append(pid)
        else:
            supplemental.append(pid)
    return exact, supplemental


# Register as Agent Tool
tool_registry.register(ToolDef(
    name="product_search",
    description="一站式商品检索：硬条件过滤 + 向量语义检索 + 多目标排序。"
                "传入结构化实体和语义查询文本，返回排序后的商品列表。",
    parameters={
        "type": "object",
        "properties": {
            "entities": {
                "type": "object",
                "description": "结构化实体（category, brand, price_max, scenario 等）",
            },
            "semantic_query": {
                "type": "string",
                "description": "语义检索文本（用户原始需求的关键词/描述）",
            },
            "top_k": {
                "type": "integer",
                "description": "向量检索数量，默认 10",
                "default": 10,
            },
            "max_results": {
                "type": "integer",
                "description": "最终返回数量（排序后截断），默认 5。推荐场景用 5，搜索场景可用 10。",
                "default": 5,
            },
            "memory_signals": {
                "type": "object",
                "description": "记忆信号（positive_interest, negative_feedback, stable_preference, recent_task_memory），用于个性化排序",
            },
        },
        "required": ["entities", "semantic_query"],
    },
    return_type="dict",
    func=product_search,
))
