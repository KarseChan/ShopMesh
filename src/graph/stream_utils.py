"""Streaming Utilities — shared helpers for SSE streaming in graph runners.

Extracts common logic from multi_agent_graph.py and shopping_agent.py:
- Search result extraction from agent output
- Product context building from state
- Narrative streaming (per-product intro → card → summary)
"""

import asyncio
import json
from collections.abc import AsyncGenerator

from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("stream_utils")

# Agent node names that produce search results
_AGENT_NODES = frozenset({
    "search_recommend_agent", "detail_compare_agent",
    "react_loop",
})

_EXPLAIN_SYSTEM_PROMPT = """你是智能导购助手。根据以下商品信息，为用户生成一段简洁的推荐总结（2-4句话）。
要求：
- 自然口语化，像朋友推荐一样
- 突出最值得买的 1-2 个商品及其亮点
- 如果有价格优势或促销信息，简要提及
- 不要罗列所有商品，只说重点
- 不要输出 JSON，直接输出文本"""


def extract_search_results_from_tool_log(tool_calls_log: list) -> list:
    """Extract search results from tool_calls_log entries.

    Searches backwards through the log for product_search or multi_query_search
    results, which contain the product list.
    """
    for entry in reversed(tool_calls_log):
        tool_name = entry.get("tool", "")
        if tool_name not in ("product_search", "multi_query_search"):
            continue
        tool_data = entry.get("result", {})
        if not isinstance(tool_data, dict):
            continue
        data = tool_data.get("data", tool_data)
        if not isinstance(data, dict):
            continue
        results = data.get("results", [])
        if results:
            return results
    return []


def extract_search_results_from_output(output: dict) -> list:
    """Extract search results from an agent node's output dict.

    Agent nodes return tool_calls_log in their output when they execute tools.
    This function extracts the search results from that log.
    """
    tool_log = output.get("tool_calls_log", [])
    if not tool_log:
        return []
    return extract_search_results_from_tool_log(tool_log)


def extract_product_context(state_values: dict) -> str:
    """Build a product context string from the final state for explanation generation.

    Extracts product names, prices, and key attributes from search results
    and recommendations to give the LLM enough context to write a summary.
    """
    # Try to get products from search_results
    search_results = state_values.get("search_results", [])

    # If not in search_results, extract from tool_calls_log
    if not search_results:
        tool_log = state_values.get("tool_calls_log", [])
        search_results = extract_search_results_from_tool_log(tool_log)

    if not search_results:
        return ""

    # Build product context
    lines = []
    for i, p in enumerate(search_results[:5], 1):
        name = p.get("name", "未知商品")
        price = p.get("price", 0)
        final_price = p.get("final_price", price)
        platform = p.get("platform_id", "")
        brand = p.get("brand", "")
        category = p.get("category", "")

        parts = [f"{i}. {name}"]
        if brand:
            parts.append(f"品牌: {brand}")
        if platform:
            parts.append(f"平台: {platform}")
        if final_price and final_price != price:
            parts.append(f"价格: ¥{price} → ¥{final_price}")
        else:
            parts.append(f"价格: ¥{price}")
        if category:
            parts.append(f"品类: {category}")

        promo = p.get("promo_desc")
        if promo:
            parts.append(f"促销: {promo}")

        lines.append(" | ".join(parts))

    # Add recommendations if available
    recommendations = state_values.get("recommendations", [])
    if recommendations:
        lines.append("\n--- 推荐信息 ---")
        for rec in recommendations[:3]:
            pid = rec.get("product_id", "")
            text = rec.get("text", "")
            if text:
                lines.append(f"- [{pid}] {text}")

    return "\n".join(lines)


def build_explanation_messages(state_values: dict) -> list[dict]:
    """Build messages for the streaming explanation LLM call.

    Uses a simple prompt (no tools) so the LLM generates natural language
    instead of structured JSON.
    """
    product_context = extract_product_context(state_values)
    if not product_context:
        return [
            {"role": "system", "content": "你是智能导购助手。"},
            {"role": "user", "content": "没有找到合适的商品，请生成一段简短的致歉和建议。"},
        ]

    # Get user's original query
    messages = state_values.get("messages", [])
    user_query = ""
    for msg in reversed(messages):
        role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "type", "")
        if role == "user":
            user_query = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            break

    return [
        {"role": "system", "content": _EXPLAIN_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户查询: {user_query}\n\n商品信息:\n{product_context}\n\n请生成推荐总结："},
    ]


async def stream_explanation(state_values: dict) -> AsyncGenerator[str, None]:
    """Generate streaming explanation via LLM.

    Yields content tokens as they arrive from the LLM.
    Falls back to the pre-computed final_response if streaming fails.
    """
    messages = build_explanation_messages(state_values)

    try:
        llm = get_llm("react_agent")
        full_text = []
        async for token in llm.chat_stream(messages):
            full_text.append(token)
            yield token

        if not full_text:
            # Streaming returned nothing, fall back to pre-computed response
            fallback = state_values.get("final_response", "")
            if fallback:
                yield fallback
    except Exception as e:
        logger.error("stream_explanation_error", error=str(e))
        # Fall back to pre-computed response
        fallback = state_values.get("final_response", "")
        if fallback:
            yield fallback


def _build_product_map(search_results: list[dict]) -> dict[str, dict]:
    """Build product_id → product dict mapping from search results."""
    product_map = {}
    for p in search_results:
        pid = p.get("product_id", "")
        if pid:
            product_map[pid] = p
    return product_map


_INTRO_SYSTEM_PROMPT = """你是智能导购助手。根据以下商品信息，为用户生成一段简洁的推荐介绍（2-3句话）。
要求：
- 自然口语化，像朋友推荐一样
- 突出这个商品的亮点（价格、品质、适用场景）
- 结合用户的具体需求和场景
- 不要输出 JSON，不要输出序号，直接输出介绍文字"""

_SUMMARY_SYSTEM_PROMPT = """你是智能导购助手。根据以下已推荐的商品，为用户生成一段简短的总结（1-2句话）。
要求：
- 自然口语化
- 概括推荐的核心理由或给出选购建议
- 不要重复每个商品的详细介绍
- 不要输出 JSON，直接输出文本"""


def _build_product_intro_messages(product: dict, user_query: str, rank: int) -> list[dict]:
    """Build LLM messages for generating a single product introduction."""
    name = product.get("name", "未知商品")
    price = product.get("price", 0)
    final_price = product.get("final_price", price)
    brand = product.get("brand", "")
    category = product.get("category", "")
    platform = product.get("platform_id", "")
    promo = product.get("promo_desc", "")
    features = product.get("features", "")

    parts = [f"商品: {name}"]
    if brand:
        parts.append(f"品牌: {brand}")
    if platform:
        parts.append(f"平台: {platform}")
    if final_price and final_price != price:
        parts.append(f"价格: ¥{price} → ¥{final_price}")
    else:
        parts.append(f"价格: ¥{price}")
    if category:
        parts.append(f"品类: {category}")
    if promo:
        parts.append(f"促销: {promo}")
    if features:
        parts.append(f"特点: {features}")

    product_info = " | ".join(parts)

    return [
        {"role": "system", "content": _INTRO_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户需求: {user_query}\n\n这是第 {rank} 个推荐商品:\n{product_info}\n\n请生成推荐介绍："},
    ]


def _build_summary_messages(products: list[dict], user_query: str) -> list[dict]:
    """Build LLM messages for generating the final summary."""
    lines = []
    for i, p in enumerate(products, 1):
        name = p.get("name", "未知商品")
        price = p.get("final_price", p.get("price", 0))
        lines.append(f"{i}. {name} (¥{price})")

    product_list = "\n".join(lines)

    return [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户需求: {user_query}\n\n已推荐的商品:\n{product_list}\n\n请生成总结："},
    ]


async def _stream_or_fallback(llm, messages: list[dict], label: str) -> AsyncGenerator[str, None]:
    """Try streaming LLM call, fall back to non-streaming if it fails.

    Yields content tokens. On streaming failure, calls llm.chat() (non-streaming)
    and yields the full text at once — better than showing nothing.
    """
    try:
        token_count = 0
        async for token in llm.chat_stream(messages):
            token_count += 1
            if token_count == 1:
                logger.info("stream_first_token", label=label)
            yield token
        logger.info("stream_complete", label=label, tokens=token_count)
    except Exception as e:
        logger.warning("stream_fallback", label=label, error_type=type(e).__name__, error=str(e))
        try:
            response = await llm.chat(messages)
            content = response.get("content", "")
            if content:
                logger.info("stream_fallback_text", label=label, text_len=len(content))
                yield content
        except Exception as e2:
            logger.error("stream_fallback_failed", label=label, error=str(e2))


def _sse(event: str, data: dict) -> dict:
    """Build SSE event dict with logging."""
    logger.info("sse_emit", event_name=event, data_keys=list(data.keys()))
    return {"event": event, "data": data}


async def stream_narrative(
    selected_product_ids: list[str],
    search_results: list[dict],
    user_query: str,
    agent_summary: str = "",
    **kwargs,
) -> AsyncGenerator[dict, None]:
    """Narrative-style streaming with real LLM calls per product.

    Makes a separate streaming LLM call for each product introduction,
    and another for the summary. Text is generated token-by-token in real-time.
    Falls back to non-streaming LLM if streaming fails.

    Args:
        selected_product_ids: Product IDs selected by the agent, in recommendation order
        search_results: Full product list from search (for product data lookup)
        user_query: The user's original query
        agent_summary: Fallback summary from agent (used if LLM summary fails)
    """
    product_map = _build_product_map(search_results)
    llm = get_llm("react_agent")

    # Build ordered product list from selected IDs
    products = []
    for pid in selected_product_ids:
        product = product_map.get(pid)
        if product:
            products.append(product)

    if not products:
        # No matching products — fall back to agent summary
        if agent_summary:
            yield _sse("explanation", {"text": agent_summary})
        return

    logger.info("narrative_begin", product_count=len(products), user_query=user_query[:50])

    # 1. Preload all product data (frontend caches, doesn't display yet)
    yield _sse("card_preload", {"products": products})

    # 2. Stream each product introduction via LLM
    all_intro_text = ""
    for i, product in enumerate(products):
        pid = product.get("product_id", "")
        rank = i + 1

        yield _sse("product_intro_start", {"product_id": pid, "index": i})

        # Generate introduction via streaming LLM call (with non-streaming fallback)
        intro_text = ""
        messages = _build_product_intro_messages(product, user_query, rank)
        async for token in _stream_or_fallback(llm, messages, f"intro_{pid}"):
            intro_text += token
            yield _sse("text_delta", {"delta": token})

        if not intro_text:
            intro_text = f"推荐 {product.get('name', '这款商品')}，价格 ¥{product.get('final_price', product.get('price', 0))}。"
            yield _sse("text_delta", {"delta": intro_text})

        all_intro_text += intro_text
        yield _sse("product_card", {"product_id": pid})
        yield _sse("product_intro_done", {"product_id": pid})

        # Brief pause between products for natural pacing
        if i < len(products) - 1:
            await asyncio.sleep(0.3)

    # 3. Stream summary via LLM
    summary_text = ""
    yield _sse("summary_start", {})
    messages = _build_summary_messages(products, user_query)
    async for token in _stream_or_fallback(llm, messages, "summary"):
        summary_text += token
        yield _sse("summary_delta", {"delta": token})

    if not summary_text:
        summary_text = agent_summary or ""
        if summary_text:
            yield _sse("summary_delta", {"delta": summary_text})

    # 4. Compat event (full text for clients that don't support narrative)
    full_text = all_intro_text + ("\n\n" + summary_text if summary_text else "")
    if full_text:
        yield _sse("explanation", {"text": full_text})


def collect_state_from_events(event_output, accumulated: dict) -> None:
    """Merge node output into accumulated state during astream_events.

    Used to collect the final state without an extra aget_state call.
    Handles reducer fields (tool_calls_log) by appending.
    Skips non-dict outputs (e.g. str returns from some nodes).
    """
    if not isinstance(event_output, dict):
        return
    for key, value in event_output.items():
        if key == "tool_calls_log":
            # Reducer field: append to existing list
            existing = accumulated.get("tool_calls_log", [])
            if isinstance(value, list):
                existing.extend(value)
            else:
                existing.append(value)
            accumulated["tool_calls_log"] = existing
        else:
            # Exclusive field: overwrite
            accumulated[key] = value
