"""Context Assembler — priority knapsack algorithm for prompt construction.

7 priority levels (P0 highest) packed into a token budget:
    P0 System Prompt (fixed)
    P1 Current user input (fixed)
    P2 L1 Working memory (fixed)
    P3 Retrieved product/promotion info (elastic)
    P4 L2c Vector-recalled history (elastic, droppable)
    P5 L2a Sliding window recent turns (elastic)
    P6 L3 User profile summary (elastic)
    P7 L2b Historical summary + broad knowledge (elastic, droppable)
"""

from src.config import config
from src.memory.compressor import estimate_tokens
from src.observability.logger import get_logger

logger = get_logger("context_assembler")

# Default token budget config
_DEFAULT_WINDOW = 8192
_BUDGET_RATIO = 0.8  # 80% for input context, 20% reserved for output

# Priority slots: (name, min_tokens, typical_tokens)
SLOTS = {
    "P0_system":       (300, 300),   # fixed
    "P1_input":        (200, 200),   # fixed
    "P2_working":      (300, 300),   # fixed
    "P3_retrieval":    (200, 800),   # elastic
    "P4_vector_recall":(0,   400),   # droppable
    "P5_window":       (100, 600),   # elastic
    "P6_profile":      (50,  200),   # elastic
    "P7_summary":      (0,   300),   # droppable
}


def _get_budget() -> int:
    ctx_window = config.get("memory", {}).get("context_window", _DEFAULT_WINDOW)
    return int(ctx_window * _BUDGET_RATIO)


def _trim_messages(messages: list[dict], max_tokens: int) -> list[dict]:
    """Trim messages to fit within token budget, keeping the most recent."""
    if not messages:
        return []
    total = sum(estimate_tokens(m.get("content", "")) for m in messages)
    if total <= max_tokens:
        return messages
    # Remove oldest messages until we fit
    trimmed = list(messages)
    while trimmed and sum(estimate_tokens(m.get("content", "")) for m in trimmed) > max_tokens:
        trimmed.pop(0)
    return trimmed


def _format_products(products: list[dict], max_tokens: int) -> str:
    """Format product list into a compact string within token budget."""
    lines = []
    for p in products:
        line = f"- {p['name']} ¥{p['price']} ({p.get('platform_id', '?')})"
        lines.append(line)
        if estimate_tokens("\n".join(lines)) > max_tokens:
            break
    return "\n".join(lines)


def _format_promotions(promotions: list[dict]) -> str:
    return "\n".join(f"- {p['description']}" for p in promotions)


async def assemble(
    system_prompt: str,
    current_input: str,
    working_memory: dict | None = None,
    retrieved_products: list[dict] | None = None,
    retrieved_promotions: list[dict] | None = None,
    vector_memories: list[dict] | None = None,
    window_messages: list[dict] | None = None,
    profile_summary: str | None = None,
    historical_summary: str | None = None,
    category_info: str | None = None,
) -> list[dict]:
    """Assemble a context-optimized messages list using priority knapsack.

    Returns a list of {"role": str, "content": str} messages ready for LLM.
    """
    budget = _get_budget()
    remaining = budget
    messages = []

    # P0: System prompt (fixed)
    sys_tokens = estimate_tokens(system_prompt)
    remaining -= sys_tokens
    messages.append({"role": "system", "content": system_prompt})

    # P1: Current input (fixed)
    input_tokens = estimate_tokens(current_input)
    remaining -= input_tokens

    # P2: Working memory (fixed)
    wm_content = ""
    if working_memory:
        wm_content = "\n".join(f"{k}: {v}" for k, v in working_memory.items() if v)
    wm_tokens = estimate_tokens(wm_content) if wm_content else 0
    remaining -= min(wm_tokens, SLOTS["P2_working"][1])

    # P3: Retrieved products (elastic)
    retrieval_content = ""
    min_r, typical_r = SLOTS["P3_retrieval"]
    if retrieved_products and remaining >= min_r:
        alloc = min(remaining, typical_r)
        if remaining >= typical_r:
            retrieval_content = _format_products(retrieved_products, alloc)
        else:
            # Trim to top-1
            retrieval_content = _format_products(retrieved_products[:1], min_r)
        remaining -= estimate_tokens(retrieval_content)

    # P4: Vector recall (droppable)
    recall_content = ""
    min_v, typical_v = SLOTS["P4_vector_recall"]
    if vector_memories and remaining >= typical_v:
        recall_lines = []
        for m in vector_memories[:3]:
            recall_lines.append(f"[历史] {m['text'][:200]}")
        recall_content = "\n".join(recall_lines)
        v_tokens = estimate_tokens(recall_content)
        if v_tokens > remaining:
            recall_content = ""
        else:
            remaining -= v_tokens
    elif vector_memories and remaining >= min_v:
        # Only keep 1
        recall_content = f"[历史] {vector_memories[0]['text'][:200]}"
        remaining -= estimate_tokens(recall_content)

    # P5: Sliding window (elastic)
    window_content = ""
    min_w, typical_w = SLOTS["P5_window"]
    if window_messages and remaining >= min_w:
        alloc = min(remaining, typical_w)
        trimmed = _trim_messages(window_messages, alloc)
        window_tokens = sum(estimate_tokens(m.get("content", "")) for m in trimmed)
        if window_tokens <= remaining:
            # Will add these as actual messages
            remaining -= window_tokens
        else:
            trimmed = _trim_messages(window_messages, min_w)
            remaining -= sum(estimate_tokens(m.get("content", "")) for m in trimmed)
        window_messages = trimmed
    else:
        window_messages = []

    # P6: Profile (elastic)
    profile_content = ""
    min_p, typical_p = SLOTS["P6_profile"]
    if profile_summary and remaining >= min_p:
        p_tokens = estimate_tokens(profile_summary)
        if p_tokens <= remaining:
            profile_content = profile_summary
        else:
            profile_content = profile_summary[:min_p * 2]  # rough char trim
        remaining -= estimate_tokens(profile_content)

    # P7: Historical summary (droppable)
    summary_content = ""
    min_s, typical_s = SLOTS["P7_summary"]
    combined_summary = ""
    if historical_summary:
        combined_summary += f"历史摘要: {historical_summary}\n"
    if category_info:
        combined_summary += category_info
    if combined_summary and remaining >= typical_s:
        summary_content = combined_summary[:typical_s * 2]
        remaining -= estimate_tokens(summary_content)

    # Assemble final messages
    # Inject P2 working memory as system message
    if wm_content:
        messages.append({"role": "system", "content": f"当前任务状态:\n{wm_content}"})

    # Inject P6 profile as system message
    if profile_content:
        messages.append({"role": "system", "content": f"用户画像: {profile_content}"})

    # Inject P7 summary as system message
    if summary_content:
        messages.append({"role": "system", "content": summary_content})

    # Inject P4 vector recall as system message
    if recall_content:
        messages.append({"role": "system", "content": f"相关历史记忆:\n{recall_content}"})

    # Inject P3 retrieval as system message
    if retrieval_content:
        promo_text = ""
        if retrieved_promotions:
            promo_text = f"\n相关促销:\n{_format_promotions(retrieved_promotions)}"
        messages.append({"role": "system", "content": f"相关商品:\n{retrieval_content}{promo_text}"})

    # P5: window messages (interleaved user/assistant)
    messages.extend(window_messages)

    # P1: current input (always last)
    messages.append({"role": "user", "content": current_input})

    total_tokens = sum(estimate_tokens(m.get("content", "")) for m in messages)
    logger.info("assembled", total_tokens=total_tokens, budget=budget,
                remaining=remaining, msg_count=len(messages))

    return messages
