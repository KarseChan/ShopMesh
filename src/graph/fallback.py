"""Fallback Node — stable pipeline backup when ReAct Agent fails.

Reuses existing preprocessing results (intent, entities, memory_chunks)
and any partial search_results the Agent may have produced.
Only fills in missing steps (search → rank → explain).
"""

from src.agents.ranker import rank
from src.observability.logger import get_logger
from src.retrieval.hybrid_retriever import hybrid_search

logger = get_logger("fallback")


def _get_user_input(state: dict) -> str:
    messages = state.get("messages", [])
    if not messages:
        return ""
    msg = messages[-1]
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


async def node_fallback(state: dict) -> dict:
    """Fallback path: reuse Agent results, fill gaps with stable pipeline.

    - Reuses: intent, entities, memory_chunks (from preprocessing)
    - Fills: search_results (if missing), ranking (if missing), explanation
    """
    entities = state.get("entities", {})
    search_results = state.get("search_results", [])

    # Step 1: If Agent hasn't searched yet, do a search
    if not search_results:
        user_input = _get_user_input(state)
        search_result = await hybrid_search(user_input, entities, top_k=10)
        search_results = search_result.get("results", [])

    # Step 2: Extract payloads from search results
    products = []
    for r in search_results:
        if "payload" in r:
            p = r["payload"]
            p["product_id"] = r.get("id", "")
            products.append(p)
        else:
            products.append(r)

    # Step 3: Rank if not already ranked
    if products and not any(p.get("rank_score") for p in products):
        products = await rank(products, entities=entities)

    # Step 4: Generate simple explanation
    if products:
        explanation = _generate_fallback_explanation(products[:3], entities)
    else:
        explanation = "抱歉，暂时没有找到符合条件的商品，建议放宽筛选条件。"

    logger.info("fallback_used",
                has_results=len(products) > 0,
                result_count=len(products),
                entities=entities)

    return {
        "search_results": products,
        "final_response": explanation,
        "used_fallback": True,
    }


def _generate_fallback_explanation(products: list, entities: dict) -> str:
    """Generate a simple, direct fallback explanation."""
    lines = ["为你推荐：\n"]
    for i, p in enumerate(products[:3], 1):
        name = p.get("name", "商品")
        price = p.get("price", 0)
        platform = p.get("platform_id", "")
        platform_tag = f" [{platform}]" if platform else ""
        lines.append(f"{i}. {name}{platform_tag} — ¥{price}")
    lines.append("\n以上是根据你的需求筛选的商品，供参考。")
    return "\n".join(lines)
