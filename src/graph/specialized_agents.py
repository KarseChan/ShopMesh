"""Specialized Agent Nodes — intent-specific ReAct loops with dedicated prompts and tools.

Contains:
- node_agent_router: deterministic routing based on intent
- node_search_recommend_agent: unified search + recommendation agent
- node_detail_compare_agent: unified detail + comparison agent
- _run_agent_loop: shared ReAct loop parameterized by agent config
- Schema validation + repair for structured JSON output
- Error Recovery: max_tokens escalation, reactive compact, fallback model
"""

import asyncio
import json
import re
import random

from src.agents.agent_config import (
    _ORDER_PLACEHOLDER,
    get_agent_config,
    get_tool_schemas_for_agent,
    is_order_intent,
    merge_agent_configs,
    resolve_agent,
    resolve_agents,
)
from src.agents.response_schemas import build_repair_prompt, parse_response_json
from src.graph.tool_executor import execute_tool
from src.models.llm_client import get_llm
from src.observability.logger import get_logger
from src.security.output_guard import OutputViolation, validate_output

logger = get_logger("specialized_agents")

# Fields that the Agent may omit but are needed for ranking
_INJECTABLE_FIELDS = ("soft_requirements", "hard_constraints", "gender", "brand")

# ── Error Recovery Constants ──
_ESCALATED_MAX_TOKENS = 64000
_DEFAULT_MAX_TOKENS = 2048
_MAX_RECOVERY_RETRIES = 3
_MAX_RETRIES = 10
_BASE_DELAY_MS = 500
_MAX_CONSECUTIVE_529 = 3
_CONTINUATION_PROMPT = (
    "输出被截断，请直接继续 — 不要道歉，不要重复之前的内容。"
    "从截断处继续完成任务。"
)


class RecoveryState:
    """Track recovery attempts across the agent loop."""

    def __init__(self):
        self.has_escalated = False
        self.recovery_count = 0
        self.consecutive_529 = 0
        self.has_attempted_reactive_compact = False
        self.current_model: str | None = None  # None = use default


def _retry_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with jitter. Retry-After takes priority."""
    if retry_after:
        return retry_after
    base = min(_BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    jitter = random.uniform(0, base * 0.25)
    return base + jitter


def _is_prompt_too_long_error(e: Exception) -> bool:
    """Check whether an API error indicates prompt/context too long."""
    msg = str(e).lower()
    return (("prompt" in msg and "long" in msg)
            or "prompt_is_too_long" in msg
            or "context_length_exceeded" in msg
            or "max_context_window" in msg)


def _is_overloaded_error(e: Exception) -> bool:
    """Check whether an API error indicates 529 overloaded."""
    msg = str(e).lower()
    return "529" in msg or "overloaded" in msg


def _is_rate_limit_error(e: Exception) -> bool:
    """Check whether an API error indicates 429 rate limit."""
    msg = str(e).lower()
    return "429" in msg or "ratelimit" in msg


async def _reactive_compact(messages: list[dict]) -> list[dict]:
    """Emergency compact — keep system prompt + last 5 messages."""
    logger.warning("reactive_compact_triggered", original_count=len(messages))
    # Keep system prompt (first message) + last 5 messages
    if len(messages) <= 6:
        return messages
    system = messages[0]
    tail = messages[-5:]
    return [system,
            {"role": "user",
             "content": "[上下文压缩] 之前的对话已精简，请从当前状态继续。"},
            *tail]


# ──────────────────────────────────────────────
# Deterministic Router Node
# ──────────────────────────────────────────────

async def node_agent_router(state: dict) -> dict:
    """Route to specialized agent based on intent. Pure if/else, no LLM.

    Supports multi-label: when user_goals contains multiple intents, merges
    agent capabilities (union tools, max iterations, primary agent's prompt).
    Handles place_order as a hardcoded branch (no ReAct loop).
    """
    intent = state.get("intent", {})
    user_goals = state.get("user_goals", [])

    # Fallback: extract from intent dict if user_goals not in state
    if not user_goals:
        if isinstance(intent, dict):
            raw = intent.get("user_goals", [])
            if isinstance(raw, list) and raw:
                user_goals = raw
            else:
                # Legacy single-value compat
                single = intent.get("user_goal", "")
                if single:
                    user_goals = [single]
        if not user_goals:
            user_goals = ["recommend_product"]

    # Handle place_order as hardcoded branch
    if is_order_intent(user_goals):
        logger.info("agent_routed_order", user_goals=user_goals)
        return {
            "active_agent": "__order__",
            "max_iterations": 1,
            "response_type": "order_confirmation",
            "final_response": "请确认是否下单。如需下单，请通过订单页面完成。",
            "recommendations": [],
            "iteration": 1,
        }

    agent_names = resolve_agents(user_goals)
    cfg = merge_agent_configs(agent_names)

    logger.info("agent_routed",
                user_goals=user_goals,
                agents=agent_names,
                primary_agent=cfg.name,
                merged_tools=cfg.tools,
                max_iterations=cfg.max_iterations,
                response_type=cfg.response_type,
                multi_label=len(agent_names) > 1)

    return {
        "active_agent": cfg.name,
        "max_iterations": cfg.max_iterations,
        "response_type": cfg.response_type,
    }


def route_to_agent(state: dict) -> str:
    """Return the graph node name for the active agent. Used in conditional edges."""
    active = state.get("active_agent", "search_recommend_agent")
    # Order is handled as hardcoded branch — route to postprocess
    if active == "__order__":
        return "__order__"
    return active


# ──────────────────────────────────────────────
# Entity Injection (from react_node.py)
# ──────────────────────────────────────────────

def _inject_entity_fields(agent_entities: dict, preprocessed_entities: dict) -> None:
    """Inject preprocessed entity fields into Agent's entities if missing."""
    for field in _INJECTABLE_FIELDS:
        if field not in agent_entities or agent_entities[field] is None:
            value = preprocessed_entities.get(field)
            if value is not None and value != [] and value != {}:
                agent_entities[field] = value


# ──────────────────────────────────────────────
# Prompt Builder Registry
# ──────────────────────────────────────────────

_PROMPT_BUILDERS = {}


def _register_prompt_builders():
    """Lazy-register prompt builder functions."""
    if _PROMPT_BUILDERS:
        return
    from src.agents.prompts.search_recommend_prompt import build_system_prompt as search_recommend_prompt
    from src.agents.prompts.detail_compare_prompt import build_system_prompt as detail_compare_prompt

    _PROMPT_BUILDERS["search_recommend_agent"] = search_recommend_prompt
    _PROMPT_BUILDERS["detail_compare_agent"] = detail_compare_prompt


# ──────────────────────────────────────────────
# Shared ReAct Loop
# ──────────────────────────────────────────────

def _build_messages(state: dict, system_prompt: str) -> list[dict]:
    """Build message list: system prompt + current user input + tool observations.

    Only includes the last user message (not full history) to avoid stale
    product data from previous turns confusing the LLM.
    """
    messages = [{"role": "system", "content": system_prompt}]

    # P1-1: Check for _force_clarification flag from deterministic missing_fields check
    task_args = state.get("_task_args", {})
    if task_args.get("_force_clarification"):
        missing_fields = task_args.get("_missing_fields", [])
        field_names = ", ".join(missing_fields)
        clarification_msg = (
            f"[系统指令] 用户缺少以下关键信息: {field_names}。\n"
            f"你必须先调用 ask_clarification 工具询问这些信息，不要直接搜索商品。\n"
            f"追问完成后，在下一轮再进行搜索。"
        )
        messages.append({"role": "user", "content": clarification_msg})
        logger.info("force_clarification_injected", fields=missing_fields)

    # Inject prior task results from DAG executor as context
    prior_results = state.get("_prior_task_results", {})
    if prior_results:
        context_parts = []
        for task_id, result in prior_results.items():
            if isinstance(result, dict) and result.get("success"):
                data = result.get("data", {})
                if isinstance(data, dict):
                    # Tool result: summarize products (supports both "data" and "results" keys)
                    products = data.get("data") or data.get("results")
                    if isinstance(products, list) and products:
                        summaries = []
                        for p in products[:5]:
                            name = p.get("name", "")
                            price = p.get("price", "")
                            pid = p.get("product_id", "")
                            summaries.append(f"  - {pid}: {name} (¥{price})")
                        context_parts.append(f"[{task_id}] 找到 {len(products)} 个商品:\n" + "\n".join(summaries))
                elif isinstance(data, dict) and "final_response" in data:
                    # Agent result: include summary
                    context_parts.append(f"[{task_id}] {data['final_response'][:200]}")
        if context_parts:
            prior_context = "已完成的前置任务结果:\n" + "\n\n".join(context_parts)
            messages.append({"role": "user", "content": prior_context})

    # Only include the last user message, skip previous turns' assistant responses
    # to avoid stale recommendation data polluting the current turn's context
    all_msgs = state.get("messages", [])
    for msg in reversed(all_msgs):
        if isinstance(msg, dict):
            role = msg.get("role", "user")
        else:
            role = getattr(msg, "type", "user")
            if role == "human":
                role = "user"
        if role == "user":
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            messages.append({"role": "user", "content": content})
            break

    for entry in state.get("tool_calls_log", []):
        tool_name = entry.get("tool", "")
        args = entry.get("args", {})
        result = entry.get("result", {})
        observation = f"调用 {tool_name}({args})\n结果: {result}"
        messages.append({"role": "assistant", "content": f"Action: {tool_name}"})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    return messages


def _parse_final_answer(content: str, response_type: str) -> dict:
    """Parse and validate Final Answer JSON. Returns parsed dict or None."""
    return parse_response_json(content, response_type)


async def _attempt_repair(raw_text: str, response_type: str, messages: list[dict], llm, tool_schemas: list[dict]) -> dict | None:
    """Attempt to repair invalid JSON output by asking LLM to fix it."""
    repair_msg = build_repair_prompt(raw_text, response_type, "JSON 格式或 schema 校验失败")
    repair_messages = messages + [
        {"role": "assistant", "content": raw_text},
        {"role": "user", "content": repair_msg},
    ]
    response = await llm.chat(repair_messages, tools=tool_schemas)
    content = response.get("content", "")
    if content:
        return _parse_final_answer(content, response_type)
    return None


def _extract_search_results_from_log(tool_log: list[dict]) -> list[dict]:
    """Extract product search results from tool call log for output guard validation."""
    search_results = []
    for entry in tool_log:
        tool_name = entry.get("tool", "")
        result = entry.get("result", {})
        data = result.get("data", result) if isinstance(result, dict) else {}

        if tool_name in ("product_search", "multi_query_search") and isinstance(data, dict):
            items = data.get("items", [])
            if items:
                search_results.extend(items)
        elif tool_name == "product_detail_batch" and isinstance(data, list):
            search_results.extend(data)

    return search_results


async def _run_agent_loop(state: dict, agent_name: str) -> dict:
    """Shared ReAct loop for all specialized agents.

    1. Get agent config (tools, prompt, response_type)
    2. Build specialized system prompt
    3. Apply four-layer compaction pipeline (P1-2)
    4. Call LLM with error recovery (max_tokens, prompt_too_long, 429/529)
    5. Execute tool or parse final answer
    6. Validate response schema, attempt repair on failure
    """
    _register_prompt_builders()

    cfg = get_agent_config(agent_name)
    tool_schemas = get_tool_schemas_for_agent(agent_name)

    # Build system prompt
    prompt_builder = _PROMPT_BUILDERS.get(agent_name)
    if prompt_builder:
        system_prompt = prompt_builder(state, cfg.tools)
    else:
        # Fallback: minimal prompt
        system_prompt = f"你是{agent_name}。请根据用户需求调用工具并给出回答。"

    messages = _build_messages(state, system_prompt)

    # ── P1-2: Initialize compaction pipeline ──
    from src.memory.context_compactor import get_compactor, CompactionCircuitBreaker
    session_id = state.get("session_id", state.get("user_id", ""))
    compactor = get_compactor(session_id=session_id)
    compaction_breaker = CompactionCircuitBreaker()

    # ── P2-1: Import hooks ──
    from src.graph.hooks import trigger_hooks

    # ── P2-2: Nag reminder constants ──
    _NAG_REMINDER_THRESHOLD = 3
    _NAG_REMINDER_MSG = (
        "请继续完成任务。如果需要更多信息，请调用 ask_clarification 工具。"
        "如果任务已完成，请输出最终结果。"
    )

    # ── Error Recovery Loop ──
    recovery = RecoveryState()
    max_tokens = _DEFAULT_MAX_TOKENS
    response = None
    rounds_since_last_tool = 0  # P2-2: Track rounds without tool calls

    for _retry in range(_MAX_RETRIES * 2):  # upper bound to prevent infinite loops
        # ── P1-2: Apply compaction before each LLM call ──
        if not compaction_breaker.is_open():
            try:
                messages = await compactor.apply_compaction(messages)
            except Exception as e:
                compaction_breaker.record_failure()
                logger.warning("compaction_pipeline_failed",
                                agent=agent_name, error=str(e))

        # ── P2-1: Pre-LLM-call hooks ──
        hook_result = await trigger_hooks("pre_llm_call", agent=agent_name, messages=messages)
        if hook_result and hook_result.block:
            return {
                "final_response": hook_result.message,
                "iteration": state.get("iteration", 0) + 1,
            }

        try:
            llm = get_llm("react_agent")
            response = await llm.chat(messages, tools=tool_schemas)
            # Success — reset consecutive 529 counter
            recovery.consecutive_529 = 0

            # ── P2-1: Post-LLM-call hooks ──
            await trigger_hooks("post_llm_call", agent=agent_name, response=response)

        except Exception as e:
            # Path 2: prompt_too_long → reactive compact (once)
            if _is_prompt_too_long_error(e):
                if not recovery.has_attempted_reactive_compact:
                    messages[:] = await _reactive_compact(messages)
                    recovery.has_attempted_reactive_compact = True
                    logger.warning("reactive_compact_retry", agent=agent_name)
                    continue
                logger.error("prompt_too_long_unrecoverable", agent=agent_name)
                return {
                    "final_response": "抱歉，对话上下文过长，请重新开始对话。",
                    "iteration": state.get("iteration", 0) + 1,
                }

            # Path 3: 529 overloaded → backoff + fallback model
            if _is_overloaded_error(e):
                recovery.consecutive_529 += 1
                if recovery.consecutive_529 >= _MAX_CONSECUTIVE_529:
                    from src.config import config as cfg_file
                    fallback = cfg_file.get("llm", {}).get("fallback", {}).get("model")
                    if fallback:
                        recovery.current_model = fallback
                        recovery.consecutive_529 = 0
                        logger.warning("fallback_model_switch",
                                       agent=agent_name, model=fallback)
                    else:
                        recovery.consecutive_529 = 0
                        logger.warning("no_fallback_configured", agent=agent_name)
                delay = _retry_delay(_retry)
                logger.warning("529_backoff", agent=agent_name,
                               delay=round(delay, 1), retry=_retry + 1)
                await asyncio.sleep(delay)
                continue

            # Path 3: 429 rate limit → backoff
            if _is_rate_limit_error(e):
                delay = _retry_delay(_retry)
                logger.warning("429_backoff", agent=agent_name,
                               delay=round(delay, 1), retry=_retry + 1)
                await asyncio.sleep(delay)
                continue

            # Unrecoverable error
            logger.error("llm_unrecoverable_error", agent=agent_name,
                         error_type=type(e).__name__, error=str(e)[:200])
            return {
                "final_response": "抱歉，AI 服务暂时不可用，请稍后再试。",
                "iteration": state.get("iteration", 0) + 1,
            }

        # ── Path 1: max_tokens → escalate or continue ──
        stop_reason = response.get("stop_reason", "")
        if stop_reason == "max_tokens":
            if not recovery.has_escalated:
                max_tokens = _ESCALATED_MAX_TOKENS
                recovery.has_escalated = True
                logger.warning("max_tokens_escalation", agent=agent_name,
                               new_max=max_tokens)
                continue  # retry same request with more tokens
            # Still truncated: save output + continuation prompt
            content = response.get("content", "")
            if content:
                messages.append({"role": "assistant", "content": content})
            if recovery.recovery_count < _MAX_RECOVERY_RETRIES:
                messages.append({"role": "user", "content": _CONTINUATION_PROMPT})
                recovery.recovery_count += 1
                logger.warning("max_tokens_continuation", agent=agent_name,
                               count=recovery.recovery_count)
                continue
            logger.warning("max_tokens_recovery_limit", agent=agent_name)
            # Fall through with whatever we have

        # ── P2-2: Nag reminder — inject if no tool calls for N rounds ──
        tool_calls_check = response.get("tool_calls") if response else None
        if not tool_calls_check:
            rounds_since_last_tool += 1
            if rounds_since_last_tool >= _NAG_REMINDER_THRESHOLD:
                logger.info("nag_reminder_triggered",
                             agent=agent_name,
                             rounds_without_tool=rounds_since_last_tool)
                messages.append({"role": "user", "content": _NAG_REMINDER_MSG})
                rounds_since_last_tool = 0
                continue  # Re-enter loop with reminder
        else:
            rounds_since_last_tool = 0  # Reset on tool call

        break  # Normal completion or exhausted retries

    if response is None:
        return {
            "final_response": "抱歉，处理过程中出现问题，请稍后再试。",
            "iteration": state.get("iteration", 0) + 1,
        }

    logger.info("llm_response", agent=agent_name,
                 has_tool_calls=bool(response.get("tool_calls")),
                 content_len=len(response.get("content", "")),
                 content_preview=response.get("content", "")[:300])

    tool_calls = response.get("tool_calls")
    content_text = response.get("content", "")

    # Detect text-based tool call attempts (LLM output "Action: xxx" instead of tool_call)
    if not tool_calls and content_text:
        text_tool_match = re.match(r"^Action:\s*(\w+)", content_text.strip())
        if text_tool_match:
            logger.warning("agent_text_tool_call_detected", agent=agent_name,
                           content=content_text[:200],
                           iteration=state.get("iteration", 0))
            # Retry: append reminder to use tool_calls and re-call LLM
            messages.append({"role": "assistant", "content": content_text})
            messages.append({"role": "user", "content":
                "请使用工具调用（tool_call）格式调用工具，不要以文本形式输出 Action。"})
            response = await llm.chat(messages, tools=tool_schemas)
            tool_calls = response.get("tool_calls")
            content_text = response.get("content", "")
            logger.info("llm_response_retry", agent=agent_name,
                         has_tool_calls=bool(tool_calls),
                         content_len=len(content_text),
                         content_preview=content_text[:300])

    if tool_calls:
        # Execute first tool call
        tc = tool_calls[0]
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        try:
            tool_args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            tool_args = {}

        logger.info("agent_action", agent=agent_name, tool=tool_name,
                     iteration=state.get("iteration", 0))

        # Defensive: inject preprocessed entity fields
        if tool_name == "product_search" and "entities" in tool_args:
            _inject_entity_fields(tool_args["entities"], state.get("entities", {}))

        # Defensive: inject search_requests from search_plan
        if tool_name == "multi_query_search":
            if "search_requests" not in tool_args or not tool_args["search_requests"]:
                search_plan = state.get("search_plan", {})
                tool_args["search_requests"] = search_plan.get("search_requests", [])
            if "entities" not in tool_args or not tool_args["entities"]:
                tool_args["entities"] = state.get("entities", {})

        result = await execute_tool(tool_name, tool_args)

        return {
            "tool_calls_log": [{
                "tool": tool_name,
                "args": tool_args,
                "result": result,
            }],
            "iteration": state.get("iteration", 0) + 1,
        }

    # No tool calls → final answer
    content = response.get("content", "")
    logger.info("agent_final", agent=agent_name,
                 iteration=state.get("iteration", 0), response_len=len(content))
    logger.info("llm_raw_output", agent=agent_name, content=content[:2000])

    # Parse + validate with schema
    parsed = _parse_final_answer(content, cfg.response_type)

    if parsed:
        logger.info("agent_schema_valid", agent=agent_name, response_type=cfg.response_type)
    elif "{" in content:
        # Only attempt repair if the response looks like it contains JSON
        logger.info("agent_schema_invalid", agent=agent_name, response_type=cfg.response_type)
        parsed = await _attempt_repair(content, cfg.response_type, messages, llm, tool_schemas)
        if parsed:
            logger.info("agent_schema_repaired", agent=agent_name)
        else:
            logger.warning("agent_schema_repair_failed", agent=agent_name)
    else:
        # Natural language response (clarification, explanation) — skip repair
        logger.info("agent_natural_response", agent=agent_name, content_len=len(content))

    # Output guard: validate recommendations against search results
    if parsed and isinstance(parsed, dict):
        output_items = parsed.get("recommendations", parsed.get("products", []))
        if output_items:
            search_results = _extract_search_results_from_log(state.get("tool_calls_log", []))
            if search_results:
                try:
                    validate_output(output_items, search_results)
                    logger.info("output_guard_passed", agent=agent_name, items=len(output_items))
                except OutputViolation as e:
                    logger.warning("output_guard_violation", agent=agent_name,
                                   violation_type=e.violation_type, reason=str(e))
                    # Don't block — inject warning into response

    # Build result
    recommendations = []
    summary = content
    selected_ids = []

    if parsed and isinstance(parsed, dict):
        # Normalize keys
        parsed = {k.strip().strip('"').strip("'").strip(): v for k, v in parsed.items()}

        # New format: agent only outputs selected_product_ids (no text)
        if "selected_product_ids" in parsed:
            selected_ids = [pid for pid in parsed["selected_product_ids"] if pid]
            logger.info("agent_selection_only", agent=agent_name, count=len(selected_ids))
        # Old format: agent outputs full recommendations with text
        elif "recommendations" in parsed:
            recommendations = parsed["recommendations"]
        elif "products" in parsed:
            recommendations = parsed["products"]

        if "summary" in parsed:
            summary = parsed["summary"]
        elif "verdict" in parsed:
            summary = parsed["verdict"]

    result = {
        "final_response": summary or content,
        "recommendations": recommendations,
        "response_type": cfg.response_type,
        "iteration": state.get("iteration", 0) + 1,
        "tool_calls_log": state.get("tool_calls_log", []),
    }

    # Set narrative streaming flags
    # New format: selected_product_ids from parsed output
    if selected_ids:
        result["selected_product_ids"] = selected_ids
        result["stream_narrative"] = True
    # Old format: extract product IDs from recommendations
    elif recommendations:
        for rec in recommendations:
            pid = rec.get("product_id", "")
            if pid:
                selected_ids.append(pid)
        if selected_ids:
            result["selected_product_ids"] = selected_ids
            result["stream_narrative"] = True

    # Merge structured data for frontend
    if parsed:
        result["response_data"] = parsed

    # Check if last action was ask_clarification with should_ask=true
    tool_log = state.get("tool_calls_log", [])
    if tool_log:
        last_tool = tool_log[-1]
        if last_tool.get("tool") == "ask_clarification":
            tool_result = last_tool.get("result", {})
            data = tool_result.get("data", tool_result)
            if data.get("should_ask"):
                entities = state.get("entities", {})
                result["pending_clarification"] = {
                    "fields": data.get("fields", []),
                    "question_spec": data.get("question_spec", {}),
                    "question_type": data.get("question_type", ""),
                    "strategy": data.get("strategy", ""),
                    "entities_snapshot": entities,
                }

    return result


# ──────────────────────────────────────────────
# Specialized Agent Node Wrappers
# ──────────────────────────────────────────────

async def node_search_recommend_agent(state: dict) -> dict:
    """Unified search + recommendation agent."""
    return await _run_agent_loop(state, "search_recommend_agent")


async def node_detail_compare_agent(state: dict) -> dict:
    """Unified detail + comparison agent."""
    return await _run_agent_loop(state, "detail_compare_agent")


# ──────────────────────────────────────────────
# Loop Control (same logic as react_node.should_continue)
# ──────────────────────────────────────────────

def should_continue(state: dict) -> str:
    """Decide whether the agent loop should continue, end, or fallback."""
    if state.get("final_response"):
        return "end"

    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 5)
    if iteration >= max_iter:
        logger.warning("agent_max_iterations", agent=state.get("active_agent"), iteration=iteration)
        return "fallback"

    log = state.get("tool_calls_log", [])
    if len(log) >= 2:
        last = log[-1]
        prev = log[-2]
        if last.get("tool") == prev.get("tool") and last.get("args") == prev.get("args"):
            logger.warning("agent_loop_detected", agent=state.get("active_agent"), tool=last.get("tool"))
            return "fallback"

    return "continue"


def route_after_agent(state: dict) -> str:
    """Route after agent node: order goes to postprocess, others use should_continue."""
    if state.get("active_agent") == "__order__":
        return "end"
    return should_continue(state)
