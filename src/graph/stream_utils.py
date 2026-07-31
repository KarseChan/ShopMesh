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


def _compose_product_intro(product: dict, rank: int) -> str:
    """确定性组装单个商品的推荐文案 —— 用排序器已算好的 rank_reason_text,不调 LLM。

    前端 ProductCard 会再展示 名称/价格/理由/促销 结构化字段,这里只需给一句
    可读的引导文案(带序号),与卡片信息一致即可。
    """
    name = product.get("name", "这款商品")
    price = product.get("final_price") or product.get("price", 0)
    reason = (product.get("rank_reason_text") or "").strip()
    promo = (product.get("promo_desc") or "").strip()

    line = f"{rank}. {name} ¥{price}"
    if reason:
        line += f" — {reason}"
    elif promo:
        line += f" — {promo}"
    return line + "\n"


def _compose_summary(products: list[dict]) -> str:
    """确定性收尾总结 —— 不调 LLM。给出数量 + 价格区间 + 一句挑选建议。"""
    if not products:
        return ""
    n = len(products)
    prices = [p.get("final_price") or p.get("price") for p in products]
    prices = [p for p in prices if p]
    if prices:
        lo, hi = min(prices), max(prices)
        rng = f"价格 ¥{lo:g}" if lo == hi else f"价格 ¥{lo:g}~¥{hi:g}"
        return f"以上为你精选的 {n} 款，{rng}。可结合尺码、品牌偏好和使用场景挑选最合适的一款～"
    return f"以上为你精选的 {n} 款商品，可结合自己的偏好挑选～"


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

    # 2. 逐商品文案:用排序器已算好的 rank_reason_text 确定性组装,不再逐个调 LLM
    #    (原来每个商品一次流式 LLM,3 个商品 ~45s;现在 0 延迟、0 token)
    all_intro_text = ""
    for i, product in enumerate(products):
        pid = product.get("product_id", "")
        rank = i + 1

        yield _sse("product_intro_start", {"product_id": pid, "index": i})

        intro_text = _compose_product_intro(product, rank)
        yield _sse("text_delta", {"delta": intro_text})

        all_intro_text += intro_text
        yield _sse("product_card", {"product_id": pid})
        yield _sse("product_intro_done", {"product_id": pid})

    # 3. 收尾总结:确定性模板(不再调 LLM,省一次 ~5-14s 的慢模型调用)
    yield _sse("summary_start", {})
    summary_text = agent_summary.strip() if agent_summary else _compose_summary(products)
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
