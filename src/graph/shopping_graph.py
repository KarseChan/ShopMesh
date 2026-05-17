"""Shopping Graph — complete reasoning chain integrating T2.1-T2.6.

Flow:
    classify_intent → [entity_extractor ∥ memory_retriever] → should_clarify?
        yes → clarification → (loop)
        no  → [hybrid_retriever ∥ promotion_calculator] → ranker → explainer → output

Latency tiers:
    Fast path (SR hit + no clarification): ~2.1s
    Standard path (LLM router + no clarification): ~3.5s
    Clarification path (1-2 rounds): ~5-8s
"""

import asyncio
import time
import uuid
from typing import AsyncGenerator

from langgraph.graph import END, StateGraph

from src.agents.clarification_engine import should_clarify
from src.agents.disambiguator import disambiguate
from src.agents.entity_extractor import extract_entities
from src.agents.explainer import generate_reason, polish_reason
from src.agents.ranker import rank, explain_rank
from src.agents.scenario_filter import filter_by_scenario
from src.graph.checkpointer import get_checkpointer
from src.graph.state import ShoppingState
from src.memory.memory_retriever import recall, should_recall, write_chunk
from src.observability.logger import get_logger, generate_request_id, set_request_context
from src.retrieval.hybrid_retriever import hybrid_search
from src.tools.promotion_calculator import calculate_promotion

logger = get_logger("shopping_graph")

# Latency tracking
_latency_stats = {"fast": [], "standard": [], "clarification": []}


def _get_user_input(state: ShoppingState) -> str:
    """Extract user input text from the last message (handles both dict and Message objects)."""
    if not state.get("messages"):
        return ""
    msg = state["messages"][-1]
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


async def node_classify_intent(state: ShoppingState) -> dict:
    """Classify user intent using Semantic Router + LLM Fallback."""
    from src.router.intent_classifier import classify_intent

    user_input = _get_user_input(state)
    intent, confidence, source = await classify_intent(user_input)

    logger.info("intent_classified", intent=intent, confidence=confidence, source=source)
    return {"intent": intent}


async def node_extract_entities(state: ShoppingState) -> dict:
    """Extract structured entities from user input."""
    user_input = _get_user_input(state)
    entities = await extract_entities(user_input)

    # Disambiguate if needed — pass raw query for when entity value is None
    if entities.get("ambiguous"):
        entities["_raw_query"] = user_input
        disambig_result = await disambiguate(entities)
        # Always use disambiguator's entities (includes resolved values or _disambiguation_question)
        entities = disambig_result["entities"]

    return {"entities": entities}


async def node_recall_memory(state: ShoppingState) -> dict:
    """Recall relevant past conversations (L2c vector memory)."""
    user_input = _get_user_input(state)
    prev_cat = state.get("entities", {}).get("category")
    current_cat = None  # Not yet extracted

    if should_recall(user_input, current_cat, prev_cat):
        memories = await recall("default_user", user_input)
        return {"memory_chunks": memories}

    return {"memory_chunks": []}


async def node_should_clarify(state: ShoppingState) -> dict:
    """Decide whether to ask a clarifying question."""
    entities = state.get("entities", {})
    asked = []  # TODO: track asked fields in state
    round_num = state.get("clarification_count", 0)

    result = await should_clarify(entities, asked, round_num)

    if result["should_ask"]:
        return {
            "clarification_count": round_num + 1,
            "explanation": result["question"],
        }
    return {"clarification_count": round_num}


async def node_clarify(state: ShoppingState) -> dict:
    """Generate clarification question to user."""
    question = state.get("explanation", "能再具体一些吗？")
    return {"explanation": question}


async def node_hybrid_retrieve(state: ShoppingState) -> dict:
    """Hybrid retrieval: semantic search + payload pre-filter."""
    user_input = _get_user_input(state)
    entities = state.get("entities", {})

    result = await hybrid_search(user_input, entities, top_k=10)
    return {"search_results": result["results"]}


async def node_promotion_calculate(state: ShoppingState) -> dict:
    """Calculate promotions for search results."""
    results = state.get("search_results", [])
    promo_info = {}

    for product in results:
        pid = product.get("payload", {}).get("product_id", product.get("id", ""))
        product_data = product.get("payload", product)
        calc = calculate_promotion(product_data, quantity=1)
        promo_info[pid] = calc

    return {"promotion_info": promo_info}


async def node_rank(state: ShoppingState) -> dict:
    """Multi-objective ranking of search results."""
    results = state.get("search_results", [])
    entities = state.get("entities", {})

    # Extract products and scores from search results
    products = []
    scores = []
    for r in results:
        payload = r.get("payload", r)
        products.append(payload)
        scores.append(r.get("score", 0.5))

    # Merge promotion info into products
    promo_info = state.get("promotion_info", {})
    for p in products:
        pid = p.get("product_id", "")
        if pid in promo_info:
            calc = promo_info[pid]
            p["final_price"] = calc.get("final_price", p.get("price", 0))
            p["promo_desc"] = calc.get("promo_desc")
            p["suggest_message"] = calc.get("suggest_message")
            p["is_abnormal"] = calc.get("is_abnormal", False)

    ranked = rank(products, search_scores=scores, entities=entities)

    # Post-filter: scenario suitability
    before_scenario = len(ranked)
    ranked = filter_by_scenario(ranked, entities)
    if len(ranked) < before_scenario:
        logger.info("scenario_post_filter", removed=before_scenario - len(ranked),
                    remaining=len(ranked))

    # Post-filter: remove over-budget items (abnormal items shown with warning in frontend)
    price_max = entities.get("price_max")
    before_count = len(ranked)
    if price_max is not None:
        ranked = [p for p in ranked if (p.get("final_price") or p.get("price", 0)) <= price_max]
    after_count = len(ranked)
    if after_count < before_count:
        logger.info("post_filter", removed=before_count - after_count,
                    budget=price_max, remaining=after_count)

    return {"ranked_results": ranked}


async def node_explain(state: ShoppingState) -> dict:
    """Generate recommendation explanations."""
    ranked = state.get("ranked_results", [])
    if not ranked:
        return {"explanation": "抱歉，没有找到符合条件的商品。"}

    # Generate reasons for top 3
    PLATFORM_NAMES = {"jd": "京东", "tb": "淘宝", "pdd": "拼多多"}
    reasons = []
    for product in ranked[:3]:
        structured = generate_reason(product)
        polished = await polish_reason(product, structured)
        platform_id = product.get("platform_id", "")
        reasons.append({
            "product": product.get("name", ""),
            "platform": PLATFORM_NAMES.get(platform_id, platform_id),
            "price": product.get("final_price", product.get("price", 0)),
            "reason": polished,
            "rank_score": product.get("rank_score", 0),
        })

    # Format output
    lines = ["为你推荐：\n"]
    for i, r in enumerate(reasons, 1):
        platform = r.get("platform", "")
        platform_tag = f" [{platform}]" if platform else ""
        lines.append(f"{i}. {r['product']}{platform_tag} — ¥{r['price']}")
        lines.append(f"   推荐理由：{r['reason']}")
        lines.append("")

    explanation = "\n".join(lines)

    # Write to memory (async, fire-and-forget)
    user_input = _get_user_input(state)
    category = state.get("entities", {}).get("category")
    asyncio.create_task(write_chunk(
        "default_user", user_input, explanation,
        entities=state.get("entities"), intent=state.get("intent"), category=category,
    ))

    return {"explanation": explanation}


def _route_after_clarify(state: ShoppingState) -> str:
    """Route after clarification check: clarify or proceed to retrieval."""
    if state.get("explanation") and state.get("clarification_count", 0) > 0:
        # Check if the clarification was just generated
        entities = state.get("entities", {})
        round_num = state.get("clarification_count", 0)
        if round_num <= 3:
            return "clarify"
    return "retrieve"


def build_shopping_graph():
    """Build the complete shopping graph with all T2.1-T2.6 nodes."""
    graph = StateGraph(ShoppingState)

    # Add nodes
    graph.add_node("classify_intent", node_classify_intent)
    graph.add_node("extract_entities", node_extract_entities)
    graph.add_node("recall_memory", node_recall_memory)
    graph.add_node("should_clarify", node_should_clarify)
    graph.add_node("clarify", node_clarify)
    graph.add_node("hybrid_retrieve", node_hybrid_retrieve)
    graph.add_node("promotion_calculate", node_promotion_calculate)
    graph.add_node("rank", node_rank)
    graph.add_node("explain", node_explain)

    # Entry point
    graph.set_entry_point("classify_intent")

    # Entity extraction and memory recall run in parallel
    graph.add_edge("classify_intent", "extract_entities")
    graph.add_edge("classify_intent", "recall_memory")

    # Both feed into clarification check
    graph.add_edge("extract_entities", "should_clarify")
    graph.add_edge("recall_memory", "should_clarify")

    # Conditional: clarify or proceed
    graph.add_conditional_edges("should_clarify", _route_after_clarify, {
        "clarify": "clarify",
        "retrieve": "hybrid_retrieve",
    })

    # Clarification loops back
    graph.add_edge("clarify", END)

    # Retrieval → promotion → rank → explain → END
    graph.add_edge("hybrid_retrieve", "promotion_calculate")
    graph.add_edge("promotion_calculate", "rank")
    graph.add_edge("rank", "explain")
    graph.add_edge("explain", END)

    checkpointer = get_checkpointer("memory")
    return graph.compile(checkpointer=checkpointer)


async def run_shopping(user_input: str, session_id: str = "default") -> dict:
    """Run the shopping graph for a single user input.

    Returns the final state with explanation and ranked results.
    """
    set_request_context(generate_request_id(), session_id)
    app = build_shopping_graph()
    start = time.time()

    initial_state = {
        "messages": [{"role": "user", "content": user_input}],
        "tool_calls": [],
        "errors": [],
        "intent": "",
        "entities": {},
        "memory_chunks": [],
        "search_results": [],
        "promotion_info": {},
        "ranked_results": [],
        "explanation": "",
        "clarification_count": 0,
        "order_info": None,
        "resume_confirmed": None,
    }

    result = await app.ainvoke(initial_state, config={"configurable": {"thread_id": session_id}})

    latency = time.time() - start

    # Classify latency tier
    intent = result.get("intent", "")
    clarification_count = result.get("clarification_count", 0)
    if clarification_count > 0:
        tier = "clarification"
    elif latency < 3.0:
        tier = "fast"
    else:
        tier = "standard"

    _latency_stats[tier].append(latency)

    logger.info("shopping_complete", latency_s=round(latency, 2), tier=tier,
                intent=intent, results=len(result.get("ranked_results", [])))

    return result


async def run_shopping_stream(user_input: str, session_id: str = "default") -> AsyncGenerator[dict, None]:
    """Run the shopping graph with SSE streaming output.

    Yields events:
        {"event": "intent", "data": {"intent": str, "confidence": float}}
        {"event": "entities", "data": {"entities": dict}}
        {"event": "clarification", "data": {"question": str}}
        {"event": "results", "data": {"products": list}}
        {"event": "explanation", "data": {"text": str}}
        {"event": "done", "data": {"latency_ms": float}}
    """
    set_request_context(generate_request_id(), session_id)
    app = build_shopping_graph()
    start = time.time()

    initial_state = {
        "messages": [{"role": "user", "content": user_input}],
        "tool_calls": [],
        "errors": [],
        "intent": "",
        "entities": {},
        "memory_chunks": [],
        "search_results": [],
        "promotion_info": {},
        "ranked_results": [],
        "explanation": "",
        "clarification_count": 0,
        "order_info": None,
        "resume_confirmed": None,
    }

    async for event in app.astream(initial_state, config={"configurable": {"thread_id": session_id}}):
        # Extract the node name and its output
        for node_name, node_output in event.items():
            if node_name == "classify_intent" and "intent" in node_output:
                yield {"event": "intent", "data": node_output}
            elif node_name == "extract_entities" and "entities" in node_output:
                yield {"event": "entities", "data": node_output}
            elif node_name == "clarify":
                yield {"event": "clarification", "data": node_output}
            elif node_name == "rank" and "ranked_results" in node_output:
                yield {"event": "results", "data": {"products": node_output["ranked_results"]}}
            elif node_name == "explain" and "explanation" in node_output:
                yield {"event": "explanation", "data": {"text": node_output["explanation"]}}

    latency_ms = (time.time() - start) * 1000
    yield {"event": "done", "data": {"latency_ms": round(latency_ms, 1)}}


def get_latency_stats() -> dict:
    """Get latency statistics by tier."""
    stats = {}
    for tier, times in _latency_stats.items():
        if times:
            stats[tier] = {
                "count": len(times),
                "avg_ms": round(sum(times) / len(times) * 1000, 1),
                "p95_ms": round(sorted(times)[int(len(times) * 0.95)] * 1000, 1) if len(times) >= 2 else round(times[0] * 1000, 1),
            }
        else:
            stats[tier] = {"count": 0, "avg_ms": 0, "p95_ms": 0}
    return stats
