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


def _normalize_answer_values(answers: dict) -> dict:
    """Normalize clarification answer option strings to usable entity values.

    Converts price options like "500-1000" → price_max=1000,
    quantity options like "2-3件" → quantity=3, etc.
    """
    import re

    result = dict(answers)

    # Price normalization
    for key in ("price_max", "price_min"):
        val = result.get(key)
        if val is None or not isinstance(val, str):
            continue
        # "100以内" → 100, "100-300" → 300/100, "300-500" → 500/300, "1000以上" → None
        if "以内" in val:
            num = re.sub(r"[^\d]", "", val)
            if num:
                result[key] = int(num)
        elif "以上" in val:
            num = re.sub(r"[^\d]", "", val)
            if key == "price_min" and num:
                result[key] = int(num)
            elif key == "price_max":
                result[key] = None  # no upper limit
        elif "-" in val:
            parts = val.split("-")
            try:
                if key == "price_max":
                    result[key] = int(parts[-1].strip())
                else:
                    result[key] = int(parts[0].strip())
            except ValueError:
                pass
        else:
            # Try direct parse
            try:
                result[key] = int(val)
            except ValueError:
                pass

    # Quantity normalization: "1件" → 1, "2-3件" → 3, "5件以上" → 5
    qty = result.get("quantity")
    if qty and isinstance(qty, str):
        nums = re.findall(r"\d+", qty)
        if nums:
            result["quantity"] = int(nums[-1])  # take the larger number

    return result


async def node_classify_intent(state: ShoppingState) -> dict:
    """Classify user intent using Semantic Router + LLM Fallback."""
    from src.router.intent_classifier import classify_intent

    user_input = _get_user_input(state)
    intent, confidence, source = await classify_intent(user_input)

    logger.info("intent_classified", intent=intent, confidence=confidence, source=source)
    return {"intent": intent}


async def node_extract_entities(state: ShoppingState) -> dict:
    """Extract structured entities from user input.

    If the user message is a JSON answer to clarification questions,
    parse it directly instead of calling the LLM.
    """
    user_input = _get_user_input(state)

    # Try parsing as JSON clarification answers
    import json as _json
    try:
        answers = _json.loads(user_input)
        if isinstance(answers, dict) and any(k in answers for k in (
            "category", "brand", "price_max", "price_min", "scenario",
            "quantity", "skin_type", "concerns", "preference",
        )):
            # Normalize option values to usable types
            answers = _normalize_answer_values(answers)
            # Merge answers into existing entities
            entities = dict(state.get("entities", {}))
            for k, v in answers.items():
                if v is not None and v != "":
                    entities[k] = v
            entities.setdefault("ambiguous", False)
            entities.setdefault("ambiguous_fields", [])
            logger.info("entities_from_answers", entities=entities)
            return {"entities": entities}
    except (ValueError, TypeError):
        pass

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
        user_id = state.get("user_id", "default_user")
        memories = await recall(user_id, user_input)
        return {"memory_chunks": memories}

    return {"memory_chunks": []}


async def node_should_clarify(state: ShoppingState) -> dict:
    """Decide whether to ask clarifying questions (batch)."""
    entities = state.get("entities", {})
    asked = list(state.get("asked_fields", []))
    round_num = state.get("clarification_count", 0)

    result = await should_clarify(entities, asked, round_num)

    if result["should_ask"]:
        questions = result.get("questions", [])
        # Track all asked fields
        for q in questions:
            field = q.get("field")
            if field and field not in asked:
                asked.append(field)
        return {
            "clarification_count": round_num + 1,
            "explanation": questions[0]["question"] if questions else "",
            "clarification_options": questions[0].get("options", []) if questions else [],
            "clarification_questions": questions,
            "asked_fields": asked,
        }
    return {"clarification_count": round_num}


async def node_clarify(state: ShoppingState) -> dict:
    """Generate clarification question to user."""
    question = state.get("explanation", "能再具体一些吗？")
    options = state.get("clarification_options", [])
    questions = state.get("clarification_questions", [])
    return {
        "explanation": question,
        "clarification_options": options,
        "clarification_questions": questions,
    }


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
    user_id = state.get("user_id", "default_user")
    category = state.get("entities", {}).get("category")
    asyncio.create_task(write_chunk(
        user_id, user_input, explanation,
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


async def run_shopping(user_input: str, session_id: str = "default", user_id: str = "default_user") -> dict:
    """Run the shopping graph for a single user input.

    Returns the final state with explanation and ranked results.
    """
    set_request_context(generate_request_id(), session_id)
    app = build_shopping_graph()
    start = time.time()

    # Only pass the new message — let other fields come from checkpoint
    # so entities, asked_fields, clarification_count persist across turns.
    initial_state = {
        "messages": [{"role": "user", "content": user_input}],
        "user_id": user_id,
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


async def run_shopping_stream(user_input: str, session_id: str = "default", user_id: str = "default_user") -> AsyncGenerator[dict, None]:
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

    # Only pass the new message — let other fields come from checkpoint
    # so entities, asked_fields, clarification_count persist across turns.
    initial_state = {
        "messages": [{"role": "user", "content": user_input}],
        "user_id": user_id,
    }

    async for event in app.astream(initial_state, config={"configurable": {"thread_id": session_id}}):
        # Extract the node name and its output
        for node_name, node_output in event.items():
            if node_name == "classify_intent" and "intent" in node_output:
                yield {"event": "intent", "data": node_output}
            elif node_name == "extract_entities" and "entities" in node_output:
                yield {"event": "entities", "data": node_output}
            elif node_name == "clarify":
                yield {"event": "clarification", "data": {
                    "explanation": node_output.get("explanation", ""),
                    "options": node_output.get("clarification_options", []),
                    "questions": node_output.get("clarification_questions", []),
                }}
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
