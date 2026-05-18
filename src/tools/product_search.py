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
) -> dict:
    """One-stop product retrieval: hybrid search + multi-objective ranking.

    Args:
        entities: Structured entities (category, brand, price_max, scenario, etc.)
        semantic_query: Semantic search text (keywords/description from user need)
        top_k: Number of results to return

    Returns:
        {"results": [...], "total": int, "filter_applied": bool, "latency_ms": float}
    """
    # Step 1: Hybrid retrieval (vector + payload pre-filter)
    search_result = await hybrid_search(semantic_query, entities, top_k=top_k)
    results = search_result["results"]

    # Extract search scores for ranking
    search_scores = [r.get("score", 0.5) for r in results]
    products = [r.get("payload", r) for r in results]

    # Attach product_id from search result id
    for i, r in enumerate(results):
        if "product_id" not in products[i]:
            products[i]["product_id"] = r.get("id", "")

    # Step 2: Multi-objective ranking
    ranked = rank(products, search_scores=search_scores, entities=entities)

    logger.info("product_search_done",
                total=len(ranked),
                filter_applied=search_result["filter_applied"],
                latency_ms=search_result["latency_ms"])

    return {
        "results": ranked,
        "total": len(ranked),
        "filter_applied": search_result["filter_applied"],
        "latency_ms": search_result["latency_ms"],
    }


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
                "description": "返回结果数量，默认 10",
                "default": 10,
            },
        },
        "required": ["entities", "semantic_query"],
    },
    return_type="dict",
    func=product_search,
))
