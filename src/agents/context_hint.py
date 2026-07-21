"""Context Hint Builder — builds context hints for entity extraction prompts.

Receives fast category detection results + previous entities + session window
+ memory recall, and constructs a hint text to inject into the LLM prompt.

This replaces the hardcoded context carry-forward (inherit/clear) logic
with a semantic hint that lets the LLM decide which fields to keep.
"""

from src.observability.logger import get_logger

logger = get_logger("context_hint")

# Fields that are category-specific and should be re-evaluated on category switch
_CATEGORY_SENSITIVE_FIELDS = ("brand", "product_type", "hard_constraints")


def _summarize_prev_entities(entities: dict) -> str:
    """Summarize non-null entity fields into a compact string."""
    summary_parts = []
    for field in ("category", "product_type", "gender", "brand", "scenario",
                  "price_min", "price_max", "skin_type"):
        val = entities.get(field)
        if val is not None and val != "" and val != []:
            summary_parts.append(f"{field}={val}")
    return ", ".join(summary_parts) if summary_parts else "（无）"


def _summarize_session_window(window: list[dict], max_turns: int = 3) -> str:
    """Summarize recent session window turns into a compact string."""
    if not window:
        return ""
    recent = window[-max_turns:]
    lines = []
    for turn in recent:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if content:
            # Truncate long messages
            if len(content) > 80:
                content = content[:80] + "..."
            lines.append(f"[{role}] {content}")
    return "\n".join(lines) if lines else ""


def _summarize_memories(memories: list[dict], max_items: int = 3) -> str:
    """Summarize recalled memory items into a compact string."""
    if not memories:
        return ""
    parts = []
    for m in memories[:max_items]:
        user_input = m.get("user_input", "")
        signal_type = m.get("memory_signal_type", "")
        if user_input:
            text = user_input[:60] + "..." if len(user_input) > 60 else user_input
            parts.append(f"[{signal_type}] {text}")
    return "\n".join(parts) if parts else ""


def build_context_hint(
    current_query: str,
    prev_entities: dict | None,
    detected_category: str | None,
    detected_confidence: float,
    session_window: list[dict] | None = None,
    memory_chunks: list[dict] | None = None,
) -> str | None:
    """Build context hint for entity extraction prompt.

    Returns a hint string to inject into the LLM prompt, or None if no
    context is available (first turn, no memory, no session).

    The hint tells the LLM:
    - What the previous turn was about (entities summary)
    - Whether the category has switched (fast detection result)
    - Instruction: ignore category-sensitive fields if switched
    - Recent conversation context (session window)
    - Relevant memory signals
    """
    prev_entities = prev_entities or {}
    session_window = session_window or []
    memory_chunks = memory_chunks or []

    # No context at all → no hint needed
    has_prev = bool(prev_entities and any(
        prev_entities.get(f) for f in ("category", "brand", "product_type", "scenario")
    ))
    has_session = bool(session_window)
    has_memory = bool(memory_chunks)

    if not has_prev and not has_session and not has_memory:
        return None

    sections = []

    # Section 1: Previous turn entities
    if has_prev:
        prev_summary = _summarize_prev_entities(prev_entities)
        prev_category = prev_entities.get("category", "")
        sections.append(f"上一轮实体：{prev_summary}")

        # Section 2: Category switch detection
        if detected_category and prev_category and detected_category != prev_category:
            # Category switched — tell LLM to be careful with old fields
            switch_fields = [f for f in _CATEGORY_SENSITIVE_FIELDS
                             if prev_entities.get(f)]
            fields_str = "、".join(switch_fields) if switch_fields else "brand、product_type"
            sections.append(
                f"品类切换：{prev_category} → {detected_category}（置信度 {detected_confidence:.2f}）\n"
                f"提示：用户本轮转向了新品类。请基于当前 query 独立提取实体，"
                f"不要沿用上轮的品类相关字段（{fields_str}），除非用户本轮明确提及。"
            )
        elif detected_category and prev_category and detected_category == prev_category:
            sections.append(
                f"品类续接：{detected_category}（与上轮一致）\n"
                f"提示：用户在同品类下继续提问，可以合理推断上下文中的品牌、类型等信息。"
            )
        elif detected_category and not prev_category:
            sections.append(
                f"本轮检测品类：{detected_category}（置信度 {detected_confidence:.2f}）"
            )

    # Section 3: Recent session window (compact)
    session_summary = _summarize_session_window(session_window, max_turns=2)
    if session_summary:
        sections.append(f"最近对话：\n{session_summary}")

    # Section 4: Memory signals (compact)
    memory_summary = _summarize_memories(memory_chunks, max_items=2)
    if memory_summary:
        sections.append(f"历史记忆：\n{memory_summary}")

    if not sections:
        return None

    hint = "\n\n".join(sections)
    logger.info("context_hint_built",
                has_prev=has_prev,
                category_switch=bool(detected_category and prev_entities.get("category")
                                     and detected_category != prev_entities.get("category")),
                sections_count=len(sections),
                hint_length=len(hint))
    return hint
