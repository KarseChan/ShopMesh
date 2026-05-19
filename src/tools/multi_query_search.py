"""Multi-Query Search Tool — batch search with plan from search_planner.

Executes multiple search requests in parallel, merges/deduplicates results,
and re-ranks across all queries.

Used when search_planner determines that a single query is insufficient
(e.g., vague product_type like "衣服" with scenario "面试").
"""

import asyncio

from src.agents.ranker import rank
from src.observability.logger import get_logger
from src.retrieval.hybrid_retriever import hybrid_search
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("multi_query_search")

# Max concurrent searches
_MAX_CONCURRENT = 5


async def multi_query_search(
    search_requests: list[dict],
    entities: dict,
    per_type_top_k: int = 5,
) -> dict:
    """Execute multiple search requests and merge results.

    Args:
        search_requests: List of {query, product_type, top_k} from search_planner
        entities: Shared entity constraints (category, gender, price_max, etc.)
        per_type_top_k: Max results per product_type group in final output

    Returns:
        {
            "results": [...],           # Merged and re-ranked products
            "total": int,               # Total unique products
            "by_type": {...},           # Products grouped by product_type
            "queries_executed": int,    # Number of queries executed
            "latency_ms": float,        # Total latency
        }
    """
    import time
    start = time.time()

    if not search_requests:
        return {"results": [], "total": 0, "by_type": {}, "queries_executed": 0, "latency_ms": 0}

    # Limit concurrency
    requests = search_requests[:_MAX_CONCURRENT]

    # Execute searches in parallel
    tasks = []
    for req in requests:
        query = req.get("query", "")
        pt = req.get("product_type")
        top_k = req.get("top_k", 10)

        # Merge product_type into entities for this specific search
        req_entities = {**entities}
        if pt:
            req_entities["product_type"] = pt

        tasks.append(hybrid_search(query, req_entities, top_k=top_k))

    search_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Merge all results, deduplicate by product_id
    seen_ids = set()
    all_products = []
    all_scores = []

    for i, result in enumerate(search_results):
        if isinstance(result, Exception):
            logger.warning("search_request_failed", index=i, error=str(result))
            continue

        for item in result.get("results", []):
            pid = item.get("id", "") or item.get("payload", {}).get("product_id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                product = item.get("payload", item)
                if "product_id" not in product:
                    product["product_id"] = pid
                all_products.append(product)
                all_scores.append(item.get("score", 0.5))

    # Re-rank merged results
    ranked = rank(all_products, search_scores=all_scores, entities=entities)

    # Group by product_type
    by_type = {}
    for p in ranked:
        pt = p.get("product_type", "other")
        if pt not in by_type:
            by_type[pt] = []
        if len(by_type[pt]) < per_type_top_k:
            by_type[pt].append(p)

    latency_ms = (time.time() - start) * 1000

    logger.info("multi_query_search_done",
                queries_executed=len(requests),
                total_products=len(ranked),
                types=list(by_type.keys()),
                latency_ms=round(latency_ms, 1))

    return {
        "results": ranked,
        "total": len(ranked),
        "by_type": {k: [p.get("product_id", "") for p in v] for k, v in by_type.items()},
        "queries_executed": len(requests),
        "latency_ms": round(latency_ms, 1),
    }


# Register as Agent Tool
tool_registry.register(ToolDef(
    name="multi_query_search",
    description="多 query 批量检索：当用户需求模糊（如'衣服'+'面试'）时，"
                "由 search_planner 生成多条检索请求，本工具并行执行并合并重排。"
                "传入 search_requests 列表和共享实体约束，返回按品类分组的排序结果。",
    parameters={
        "type": "object",
        "properties": {
            "search_requests": {
                "type": "array",
                "description": "检索请求列表，来自 search_planner 的输出",
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "语义查询文本"},
                        "product_type": {"type": "string", "description": "商品类型"},
                        "top_k": {"type": "integer", "description": "返回数量", "default": 10},
                    },
                    "required": ["query"],
                },
            },
            "entities": {
                "type": "object",
                "description": "共享实体约束（category, gender, price_max 等）",
            },
            "per_type_top_k": {
                "type": "integer",
                "description": "每个品类最多返回数量，默认 5",
                "default": 5,
            },
        },
        "required": ["search_requests", "entities"],
    },
    return_type="dict",
    func=multi_query_search,
))
