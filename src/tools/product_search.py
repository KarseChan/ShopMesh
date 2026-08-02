"""Product Search Tool — one-stop retrieval for ReAct Agent.

Internal flow:
1. hybrid_search (Qdrant vector + payload pre-filter)
2. rank (multi-objective weighted fusion)

Agent calls this single tool instead of managing low-level retrieval details.
"""

from src.agents.ranker import rank
from src.agents.scenario_filter import filter_by_scenario
from src.models.embedder import get_embedder
from src.observability.logger import get_logger
from src.retrieval.hybrid_retriever import hybrid_search
from src.tools.agent_tools import constraint_relaxation
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("product_search_tool")

# Identity constraints never auto-relaxed: we widen budget/brand/preference to
# find *something*, but never swap the kind of product the user asked for.
_PROTECTED_ON_RELAX = ["product_type", "category"]

# Cap on auto-relaxation rounds (each round relaxes one constraint + re-searches).
_MAX_RELAX_ROUNDS = 4

# Candidate pool size when surfacing cheapest alternatives after a budget relax.
# Larger than the usual top_k so the price sort sees genuinely cheap matches.
_CHEAPEST_POOL_K = 100


def _post_filter(products: list[dict], entities: dict) -> list[dict]:
    """Hard post-filters applied after ranking (P0-2 regression guards).

    These are safety nets independent of the retrieval-layer Qdrant filter:
    even if entity extraction or the vector pre-filter misses, over-budget or
    scenario-inappropriate items never reach the user.

    1. Budget: drop items outside [price_min, price_max] (P0 bug — ¥549 for "500以内").
    2. Scenario: drop category-inappropriate items (P1 bug — 奶茶 for "送礼物").
    """
    price_min = entities.get("price_min")
    price_max = entities.get("price_max")

    kept = []
    dropped_budget = []
    for p in products:
        price = p.get("price")
        if price is not None:
            if price_max is not None and price > float(price_max):
                dropped_budget.append(p.get("product_id", ""))
                continue
            if price_min is not None and price < float(price_min):
                dropped_budget.append(p.get("product_id", ""))
                continue
        kept.append(p)

    if dropped_budget:
        logger.info("budget_post_filter",
                    price_min=price_min, price_max=price_max,
                    dropped=len(dropped_budget), remaining=len(kept))

    # Scenario category whitelist/blacklist (e.g. gift → exclude 奶茶/食品/家居)
    kept = filter_by_scenario(kept, entities)
    return kept


async def _retrieve_and_rank(
    entities: dict,
    semantic_query: str,
    query_vector: list[float],
    top_k: int,
    max_results: int,
    memory_signals: dict | None,
    prefer_cheapest: bool = False,
) -> tuple[list[dict], dict]:
    """Single retrieve → rank → hard-filter pass. Returns (ranked, search_meta).

    Factored out so the auto-relaxation loop can re-run it with relaxed entities
    while reusing the same precomputed query embedding.

    prefer_cheapest: when the budget ceiling was relaxed (nothing met the user's
        max price), sort ascending by price BEFORE capping, so we surface the
        cheapest available — genuinely "closest to your budget" rather than the
        most semantically-relevant (and often most expensive) items.
    """
    # Step 1: Hybrid retrieval (vector + payload pre-filter).
    # When preferring cheapest (budget relaxed), widen the candidate pool so the
    # price sort sees the globally cheapest matches, not just the cheapest among
    # the top-K by relevance (the cheapest item is often not the most relevant).
    retrieval_k = max(top_k, _CHEAPEST_POOL_K) if prefer_cheapest else top_k
    search_result = await hybrid_search(semantic_query, entities, top_k=retrieval_k,
                                        query_vector=query_vector)
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

    # Step 3.5: Hard post-filters (budget + scenario) — P0-2 safety nets.
    # NOTE: uses the SAME (possibly relaxed) entities, so a relaxed price ceiling
    # is honored here too — over-budget items only survive when we intentionally
    # relaxed the budget, never due to a filter miss.
    ranked = _post_filter(ranked, entities)

    # When the budget ceiling was relaxed, cheapest-first = closest to the user's
    # intent (they asked for "at most ¥X"). Sort before capping so the top items
    # are the globally cheapest, not the cheapest among the top-K by relevance.
    if prefer_cheapest:
        ranked = sorted(ranked, key=lambda p: (p.get("price") is None, p.get("price") or 0))

    # Step 4: Cap results to save LLM tokens
    ranked = ranked[:max_results]
    return ranked, search_result


async def product_search(
    entities: dict,
    semantic_query: str,
    top_k: int = 10,
    max_results: int = 10,
    memory_signals: dict | None = None,
) -> dict:
    """One-stop product retrieval: hybrid search + multi-objective ranking.

    On empty results, deterministically relaxes the least-important constraint
    (budget/brand/preference/scenario — never product_type/category) and
    re-searches, so an unsatisfiable request (e.g. "面霜 ≤¥300" when the cheapest
    is ¥574) returns the closest labeled alternative instead of nothing. This is
    done in code rather than left to the LLM to notice, so it always fires.

    Args:
        entities: Structured entities (category, brand, price_max, scenario, etc.)
        semantic_query: Semantic search text (keywords/description from user need)
        top_k: Number of results to retrieve from vector search
        max_results: Max results to return (caps ranked output, saves LLM tokens)

    Returns:
        {"results": [...], "total": int, "filter_applied": bool, "latency_ms": float,
         "relaxed": bool, "relaxed_constraints": [...], "relaxation_note": str}
    """
    # Embed once — the relaxation loop re-searches with the same query text.
    query_vector = await get_embedder().aembed(semantic_query)

    search_entities = dict(entities)
    ranked, search_result = await _retrieve_and_rank(
        search_entities, semantic_query, query_vector, top_k, max_results, memory_signals)

    # Step 3.6: Auto-relaxation — if empty, widen constraints one at a time.
    orig_had_price_max = entities.get("price_max") is not None
    relaxed_constraints: list[str] = []
    if not ranked:
        for _ in range(_MAX_RELAX_ROUNDS):
            relax = await constraint_relaxation(
                search_entities, "结果为空", protected_fields=_PROTECTED_ON_RELAX)
            if not relax["relaxed"]:
                break  # nothing left to relax (only protected fields remain)
            search_entities = relax["entities"]
            relaxed_constraints.extend(relax["relaxed"])
            # If the user's price ceiling has been dropped, surface cheapest-first.
            prefer_cheapest = orig_had_price_max and search_entities.get("price_max") is None
            ranked, search_result = await _retrieve_and_rank(
                search_entities, semantic_query, query_vector, top_k, max_results,
                memory_signals, prefer_cheapest=prefer_cheapest)
            if ranked:
                break
        if relaxed_constraints:
            logger.info("product_search_relaxed",
                        relaxed=relaxed_constraints, recovered=len(ranked))

    # Step 5: Split exact vs supplemental matches (use relaxed entities)
    product_type = search_entities.get("product_type")
    exact_ids, supplemental_ids = _split_matches(ranked, product_type)

    logger.info("product_search_done",
                total=len(ranked),
                exact=len(exact_ids),
                supplemental=len(supplemental_ids),
                filter_applied=search_result["filter_applied"],
                latency_ms=search_result["latency_ms"])

    # Step 6: Build slim results for Agent prompt + full results for result store
    slim_results = [_slim_product(p) for p in ranked]

    relaxation_note = ""
    if relaxed_constraints:
        relaxation_note = (
            "没有完全符合条件的商品，已为你放宽：" + "、".join(relaxed_constraints)
            + "，以下是最接近的结果。"
        )

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
        "relaxed": bool(relaxed_constraints),
        "relaxed_constraints": relaxed_constraints,
        "relaxation_note": relaxation_note,
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
        "image_url": p.get("image_url", ""),  # 前端 ProductCard 展示用(对 LLM 无意义但成本可忽略)
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
