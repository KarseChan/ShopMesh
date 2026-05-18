"""ReAct Agent Prompt — system prompt and message builder for the shopping agent.

Constructs the prompt that guides the LLM through Thought → Action → Observation
reasoning, with preprocessed context (intent, entities, memories) injected.
"""

from src.tools.registry import get_dynamic_tools

_SYSTEM_TEMPLATE = """你是一个智能导购 Agent。系统已经为你完成了以下预处理：

用户意图：{intent}（置信度 {confidence}）
提取的实体：{entities}
用户历史记忆：{memory_summary}

你可以调用以下工具来完成后续决策：

{tool_descriptions}

遵循 ReAct 模式：
1. Thought: 分析当前状态，决定下一步
2. Action: 调用一个工具
3. Observation: 观察工具返回结果
4. 重复直到信息充足，然后给出 Final Answer

决策规则：
- 实体已完整 + 无歧义 → 调用 product_search 直接检索
- 实体缺失关键字段（如品类为空） → 调用 ask_clarification
- 记忆中有用户偏好 → 用记忆补全实体，不追问，直接检索
- 检索结果 < 3 → 调用 constraint_relaxation 放宽后重新 product_search
- 意图是 compare → 调用 product_detail_batch 获取详情，再调用 price_compare
- 每次只调用一个工具
- 不要重复调用已调用过的工具（相同参数）
- 信息充足时直接给出 Final Answer，不要多余调用"""


def _format_entities(entities: dict) -> str:
    """Format entities dict for prompt display."""
    if not entities:
        return "无"
    parts = []
    for k, v in entities.items():
        if v is None or k.startswith("_") or k in ("ambiguous", "ambiguous_fields"):
            continue
        parts.append(f"{k}={v}")
    return ", ".join(parts) if parts else "无"


def _format_memory(memory_chunks: list) -> str:
    """Format memory chunks for prompt display."""
    if not memory_chunks:
        return "无历史记忆"
    lines = []
    for m in memory_chunks[:3]:  # Limit to 3 to control prompt size
        text = m.get("text", "")
        score = m.get("score", 0)
        if text:
            lines.append(f"- [{score:.2f}] {text[:100]}")
    return "\n".join(lines) if lines else "无历史记忆"


def _format_tool_descriptions(tools: list) -> str:
    """Format tool list for prompt display."""
    lines = []
    for t in tools:
        lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


def build_system_prompt(state: dict) -> str:
    """Build the ReAct system prompt with preprocessed context.

    Args:
        state: AgentState containing intent, entities, memory_chunks

    Returns:
        Formatted system prompt string
    """
    intent = state.get("intent", "unknown")
    confidence = state.get("_intent_confidence", 0.8)
    entities = state.get("entities", {})
    memory_chunks = state.get("memory_chunks", [])

    tools = get_dynamic_tools()

    return _SYSTEM_TEMPLATE.format(
        intent=intent,
        confidence=f"{confidence:.2f}",
        entities=_format_entities(entities),
        memory_summary=_format_memory(memory_chunks),
        tool_descriptions=_format_tool_descriptions(tools),
    )


def build_react_messages(state: dict) -> list[dict]:
    """Build the full message list for ReAct LLM call.

    Returns: [system_prompt, ...dialog_history]
    """
    system_prompt = build_system_prompt(state)

    messages = [{"role": "system", "content": system_prompt}]

    # Append dialog history
    for msg in state.get("messages", []):
        if isinstance(msg, dict):
            messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        else:
            role = getattr(msg, "type", "user")
            if role == "human":
                role = "user"
            elif role == "ai":
                role = "assistant"
            messages.append({"role": role, "content": getattr(msg, "content", "")})

    # Append tool call observations from previous iterations
    for entry in state.get("tool_calls_log", []):
        tool_name = entry.get("tool", "")
        args = entry.get("args", {})
        result = entry.get("result", {})
        observation = f"调用 {tool_name}({args})\n结果: {result}"
        messages.append({"role": "assistant", "content": f"Action: {tool_name}"})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    return messages
