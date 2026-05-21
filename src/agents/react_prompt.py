"""ReAct Agent Prompt — system prompt and message builder for the shopping agent.

Constructs the prompt that guides the LLM through Thought → Action → Observation
reasoning, with preprocessed context (intent, entities, memories) injected.
"""

from src.tools.registry import get_dynamic_tools

_SYSTEM_TEMPLATE = """你是一个智能导购 Agent。系统已经为你完成了以下预处理：

用户意图：{intent}（置信度 {confidence}）
提取的实体：{entities}
用户历史记忆：{memory_summary}
检索计划：{search_plan}

你可以调用以下工具来完成后续决策：

{tool_descriptions}

遵循 ReAct 模式：
1. Thought: 分析当前状态，决定下一步
2. Action: 调用一个工具
3. Observation: 观察工具返回结果
4. 重复直到信息充足，然后给出 Final Answer

决策规则（按优先级）：
1. 实体有 missing_critical_fields → 先调用 ask_clarification，根据返回的 strategy 决定：
   - strategy=ask → 向用户追问。根据返回的 question_spec 生成自然追问文本：
     * question_spec.must_ask: 必须问的字段
     * question_spec.context: 场景上下文（如"面试"），融入追问文本
     * question_spec.suggestions: 建议选项（如 product_type: ["衬衫","西装外套"]），引导用户选择
     * 追问文本要自然流畅，不要机械罗列字段名，例如"你想看男装还是女装？面试的话，我建议优先看衬衫或西装外套。"
   - strategy=light_ask → 轻量追问，用户可跳过，语气更随意
   - strategy=assume → 不追问，用 assumptions 补全实体，在回复中声明假设
2. 检索计划 search_mode=outfit_multi_query → 调用 multi_query_search，传入 search_requests 和 entities
3. 检索计划 search_mode=single 或无检索计划 → 调用 product_search 直接检索
4. 记忆中有用户偏好 → 用记忆补全实体，不追问，直接检索
5. product_search 返回结果 < 3 → 调用 constraint_relaxation 放宽约束，拿到返回的 entities 后必须立即用它重新调用 product_search
6. 意图是 compare → 调用 product_detail_batch 获取详情，再调用 price_compare
- 每次只调用一个工具
- 不要重复调用已调用过的工具（相同参数）
- 信息充足时直接给出 Final Answer，不要多余调用
- 严禁凭空编造商品信息，所有推荐必须基于工具返回的真实数据

重要：调用 product_search 时，entities 参数必须使用上面"提取的实体"中的完整实体对象，不要自行重建或省略字段。soft_requirements、hard_constraints、gender 等字段对排序至关重要。

Final Answer 格式：
当你完成检索并准备推荐商品时，必须输出 JSON 格式（不要输出其他文字）：

```json
{{
  "recommendations": [
    {{"product_id": "商品ID", "text": "推荐理由（2-3句话，结合用户需求说明为什么推荐）"}},
    {{"product_id": "商品ID", "text": "推荐理由"}}
  ],
  "summary": "总结语（1句话，如'综合你的需求，前两款最推荐'）"
}}
```

- recommendations 按推荐优先级排序，product_id 必须是工具返回的真实商品 ID
- 每个 text 要个性化，结合用户的具体需求（场景、偏好、预算等）
- summary 是整体总结，放在推荐列表之后
- 如果只找到 1 个商品，recommendations 只放 1 个
- 如果没有找到商品，recommendations 为空数组，summary 说明原因"""


def _format_entities(entities: dict) -> str:
    """Format entities dict for prompt display.

    Outputs structured fields as JSON so the LLM can pass them verbatim
    to product_search without losing soft_requirements/hard_constraints.
    """
    import json as _json

    if not entities:
        return "无"
    parts = []
    for k, v in entities.items():
        if v is None or k.startswith("_") or k in ("ambiguous", "ambiguous_fields"):
            continue
        # List/dict fields: output as JSON for LLM to copy verbatim
        if isinstance(v, (list, dict)):
            if v:  # non-empty
                parts.append(f"{k}={_json.dumps(v, ensure_ascii=False)}")
            continue
        parts.append(f"{k}={v}")

    # Always show missing_critical_fields if present
    missing = entities.get("missing_critical_fields", [])
    if missing:
        parts.append(f"⚠️ 缺失关键字段: {missing}")

    return ", ".join(parts) if parts else "无"


def _format_memory(memory_chunks: list) -> str:
    """Format memory chunks for prompt display."""
    if not memory_chunks:
        return "无历史记忆"
    lines = []
    for m in memory_chunks[:3]:  # Limit to 3 to control prompt size
        text = m.get("text", "")
        score = m.get("score", 0)
        days = m.get("days_old", 0)
        time_label = f"{days}天前" if days > 0 else "今天"
        if text:
            lines.append(f"- [{time_label}] {text[:100]}")
    return "\n".join(lines) if lines else "无历史记忆"


def _format_session_summary(summary: str) -> str:
    """Format L2b session summary for prompt display."""
    if not summary:
        return ""
    return f"历史摘要: {summary}"


def _format_user_profile(profile: dict) -> str:
    """Format L3 user profile for prompt display."""
    if not profile:
        return ""
    parts = []
    if profile.get("preferred_brands"):
        parts.append(f"偏好品牌: {', '.join(profile['preferred_brands'][:3])}")
    sensitivity = profile.get("price_sensitivity")
    if sensitivity is not None and sensitivity != 0.5:
        level = "高" if sensitivity > 0.7 else "低"
        parts.append(f"价格敏感度: {level}")
    if profile.get("price_range"):
        pr = profile["price_range"]
        parts.append(f"预算区间: {pr[0]}-{pr[1]}元")
    if profile.get("scenario"):
        parts.append(f"使用场景: {profile['scenario']}")
    return "，".join(parts) if parts else ""


def _format_search_plan(search_plan: dict) -> str:
    """Format search plan for prompt display."""
    if not search_plan:
        return "无（直接检索）"

    mode = search_plan.get("search_mode", "single")
    reason = search_plan.get("reason", "")

    if mode == "single":
        return "单次检索（商品类型明确）"

    # Multi-query mode
    parts = [f"模式：多 query 检索"]
    if reason:
        parts.append(f"原因：{reason}")

    target_types = search_plan.get("target_product_types", [])
    if target_types:
        parts.append(f"目标品类：{', '.join(target_types)}")

    requests = search_plan.get("search_requests", [])
    if requests:
        parts.append(f"检索请求数：{len(requests)}")
        for i, req in enumerate(requests[:5], 1):
            q = req.get("query", "")
            pt = req.get("product_type", "不限")
            parts.append(f"  {i}. [{pt}] {q}")

    return "\n".join(parts)


def _format_intent(intent: dict | str) -> str:
    """Format intent dict for prompt display."""
    if isinstance(intent, str):
        return intent
    if not intent or not isinstance(intent, dict):
        return "未知"
    user_goal = intent.get("user_goal", "")
    task_type = intent.get("task_type", "")
    execution_hint = intent.get("execution_hint", "")
    return f"{user_goal} / {task_type} / {execution_hint}"


def _format_tool_descriptions(tools: list) -> str:
    """Format tool list for prompt display."""
    lines = []
    for t in tools:
        lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


def build_system_prompt(state: dict) -> str:
    """Build the ReAct system prompt with preprocessed context.

    Args:
        state: AgentState containing intent, entities, memory_chunks, search_plan,
               session_summary, user_profile

    Returns:
        Formatted system prompt string
    """
    intent = state.get("intent", {})
    confidence = state.get("_intent_confidence", 0.8)
    entities = state.get("entities", {})
    memory_chunks = state.get("memory_chunks", [])
    search_plan = state.get("search_plan", {})
    session_summary = state.get("session_summary", "")
    user_profile = state.get("user_profile", {})

    tools = get_dynamic_tools()

    # Build context sections
    context_parts = []

    profile_str = _format_user_profile(user_profile)
    if profile_str:
        context_parts.append(f"【用户画像】{profile_str}")

    summary_str = _format_session_summary(session_summary)
    if summary_str:
        context_parts.append(f"【历史脉络】{summary_str}")

    memory_str = _format_memory(memory_chunks)
    if memory_str and memory_str != "无历史记忆":
        context_parts.append(f"【历史事实】\n{memory_str}")

    context_block = "\n".join(context_parts) if context_parts else ""

    system_prompt = _SYSTEM_TEMPLATE.format(
        intent=_format_intent(intent),
        confidence=f"{confidence:.2f}",
        entities=_format_entities(entities),
        memory_summary=memory_str,
        search_plan=_format_search_plan(search_plan),
        tool_descriptions=_format_tool_descriptions(tools),
    )

    # Prepend context block (profile + summary) before the main system prompt
    if context_block:
        system_prompt = f"{context_block}\n\n{system_prompt}"

    return system_prompt


def build_react_messages(state: dict) -> list[dict]:
    """Build the full message list for ReAct LLM call.

    Returns: [system_prompt, ...session_window, ...dialog_history, ...tool_observations]
    """
    system_prompt = build_system_prompt(state)
    messages = [{"role": "system", "content": system_prompt}]

    # Inject L2a session window (historical turns from Redis)
    session_window = state.get("session_window", [])
    if session_window:
        messages.extend(session_window)

    # Append current dialog history
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
