"""ReAct Loop Node — core reasoning loop for the shopping Agent.

Implements Thought → Action → Observation cycle:
1. Build messages with preprocessed context + tool call history
2. LLM decides next action (tool call or final answer)
3. Execute tool and return observation, or return final_response
"""

import json
import re

from src.agents.react_prompt import build_react_messages
from src.graph.tool_executor import execute_tool
from src.models.llm_client import get_llm
from src.observability.logger import get_logger
from src.tools.registry import get_all_tool_schemas

logger = get_logger("react_node")

# Fields that the Agent may omit but are needed for ranking
_INJECTABLE_FIELDS = ("soft_requirements", "hard_constraints", "gender", "brand")


def _inject_entity_fields(agent_entities: dict, preprocessed_entities: dict) -> None:
    """Inject preprocessed entity fields into Agent's entities if missing.

    The Agent reconstructs entities from the prompt and may drop fields like
    soft_requirements. This function merges them back from preprocessing state.
    """
    for field in _INJECTABLE_FIELDS:
        if field not in agent_entities or agent_entities[field] is None:
            value = preprocessed_entities.get(field)
            if value is not None and value != [] and value != {}:
                agent_entities[field] = value


def _compact_for_react(messages: list[dict], iteration: int) -> list[dict]:
    """Zero-API compaction for ReAct messages.

    iteration 0: no compaction (first call needs full context)
    iteration 1+: compress prior tool observations to summary

    Returns compacted message list.
    """
    if iteration == 0:
        return messages

    # Find and compress old tool observation pairs (Action + Observation)
    # Keep the most recent iteration's observations intact
    compacted = []
    observation_pairs = []
    non_observation = []

    i = 0
    while i < len(messages):
        msg = messages[i]
        content = msg.get("content", "")
        # Detect Action/Observation pairs from tool_calls_log
        if (msg.get("role") == "assistant" and content.startswith("Action: ")
                and i + 1 < len(messages)
                and messages[i + 1].get("role") == "user"
                and messages[i + 1].get("content", "").startswith("Observation: ")):
            observation_pairs.append((i, messages[i], messages[i + 1]))
            i += 2
        else:
            non_observation.append((i, msg))
            i += 1

    if len(observation_pairs) <= 1:
        return messages  # Nothing to compact

    # Keep all non-observation messages as-is
    for _, msg in non_observation:
        compacted.append(msg)

    # Insert all but the last observation pair as compressed summary
    old_pairs = observation_pairs[:-1]
    last_pair = observation_pairs[-1]

    # Build summary of old tool calls
    old_tool_names = []
    for _, action_msg, _ in old_pairs:
        tool_name = action_msg.get("content", "").replace("Action: ", "")
        old_tool_names.append(tool_name)

    summary = f"[已压缩 {len(old_pairs)} 轮工具调用: {', '.join(old_tool_names)}]"
    # Insert summary before the last observation pair
    # Find the right position: after non-observation messages that come before the last pair
    last_pair_idx = last_pair[0]
    result = []
    for msg in compacted:
        result.append(msg)
    result.append({"role": "user", "content": summary})
    result.extend([last_pair[1], last_pair[2]])

    logger.info("react_compact_applied",
                 compressed_pairs=len(old_pairs),
                 kept_pair=last_pair[1].get("content", "")[:30])
    return result


async def node_react_loop(state: dict) -> dict:
    """ReAct core loop: Thought → Action → Observation.

    One iteration per call. LangGraph loops via should_continue conditional edge.

    Input: preprocessed intent, entities, memories + dialog history
    Output: tool_calls_log entry OR final_response
    """
    llm = get_llm("react_agent")
    tool_schemas = get_all_tool_schemas()

    # Build messages with context + prior observations
    messages = build_react_messages(state)

    # Compact prior tool observations on iteration 1+ (zero API cost)
    iteration = state.get("iteration", 0)
    messages = _compact_for_react(messages, iteration)

    # LLM decides next action
    response = await llm.chat(messages, tools=tool_schemas)

    tool_calls = response.get("tool_calls")
    if tool_calls:
        # Execute first tool call (we only support one per iteration)
        tc = tool_calls[0]
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        try:
            tool_args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            tool_args = {}

        logger.info("react_action", tool=tool_name, iteration=state.get("iteration", 0))

        # Defensive: inject preprocessed entity fields into product_search if Agent omitted them
        if tool_name == "product_search" and "entities" in tool_args:
            _inject_entity_fields(tool_args["entities"], state.get("entities", {}))

        # Defensive: inject search_requests from search_plan into multi_query_search
        if tool_name == "multi_query_search":
            if "search_requests" not in tool_args or not tool_args["search_requests"]:
                search_plan = state.get("search_plan", {})
                tool_args["search_requests"] = search_plan.get("search_requests", [])
            if "entities" not in tool_args or not tool_args["entities"]:
                tool_args["entities"] = state.get("entities", {})

        # Defensive: inject memory_signals for personalized ranking
        if tool_name in ("product_search", "multi_query_search"):
            if "memory_signals" not in tool_args or not tool_args["memory_signals"]:
                tool_args["memory_signals"] = state.get("memory_signals", {})

        result = await execute_tool(tool_name, tool_args)

        return {
            "tool_calls_log": [{
                "tool": tool_name,
                "args": tool_args,
                "result": result,
            }],
            "iteration": state.get("iteration", 0) + 1,
        }
    else:
        # LLM gave a final answer
        content = response.get("content", "")
        logger.info("react_final", iteration=state.get("iteration", 0), response_len=len(content))

        # Try to parse structured recommendations from the response
        recommendations = []
        summary = content  # fallback: treat entire content as summary
        selected_ids = []

        try:
            # Extract JSON from possible code fences
            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()

            # Fallback: find first { ... } block via regex
            if not json_str.strip().startswith("{"):
                match = re.search(r"\{[\s\S]*\}", json_str)
                if match:
                    json_str = match.group(0)

            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                # Normalize keys: strip whitespace/newlines/embedded quotes that LLM may inject
                parsed = {k.strip().strip('"').strip("'").strip(): v for k, v in parsed.items()}

                # New format: agent only outputs selected_product_ids
                if "selected_product_ids" in parsed:
                    selected_ids = [pid for pid in parsed["selected_product_ids"] if pid]
                    logger.info("structured_selection_parsed", count=len(selected_ids))
                # Old format: agent outputs full recommendations with text
                elif "recommendations" in parsed:
                    recommendations = parsed["recommendations"]
                    summary = parsed.get("summary", "")
                    logger.info("structured_response_parsed",
                                recommendation_count=len(recommendations),
                                summary_len=len(summary))

                # Output guard: validate recommendations against search results
                output_items = recommendations
                if output_items:
                    from src.graph.specialized_agents import _extract_search_results_from_log
                    from src.security.output_guard import OutputViolation, validate_output
                    search_results = _extract_search_results_from_log(state.get("tool_calls_log", []))
                    if search_results:
                        try:
                            validate_output(output_items, search_results)
                            logger.info("output_guard_passed", items=len(output_items))
                        except OutputViolation as e:
                            logger.warning("output_guard_violation",
                                           violation_type=e.violation_type, reason=str(e))

        except (json.JSONDecodeError, IndexError, KeyError, TypeError):
            # Not structured JSON — use content as-is (backward compatible)
            logger.info("structured_response_fallback", reason="json_parse_failed")

        result = {
            "final_response": summary or content,
            "recommendations": recommendations,
            "iteration": state.get("iteration", 0) + 1,
        }

        # Set narrative streaming flags
        if selected_ids:
            result["selected_product_ids"] = selected_ids
            result["stream_narrative"] = True
        elif recommendations:
            for rec in recommendations:
                pid = rec.get("product_id", "")
                if pid:
                    selected_ids.append(pid)
            if selected_ids:
                result["selected_product_ids"] = selected_ids
                result["stream_narrative"] = True

        # Check if the last action was ask_clarification with should_ask=true
        # If so, set pending_clarification for the next turn
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
                    logger.info("pending_clarification_set",
                                fields=data.get("fields", []),
                                question_type=data.get("question_type", ""))

        return result


def should_continue(state: dict) -> str:
    """Decide whether ReAct loop should continue, end, or fallback.

    Returns:
        "end" — final_response produced, go to postprocess
        "fallback" — max iterations or loop detected, go to fallback
        "continue" — keep looping
    """
    # 1. Final response → normal end
    if state.get("final_response"):
        return "end"

    # 2. Max iterations reached → fallback
    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", 5)
    if iteration >= max_iter:
        logger.warning("react_max_iterations", iteration=iteration)
        return "fallback"

    # 3. Loop detection: same tool + same args twice in a row
    log = state.get("tool_calls_log", [])
    if len(log) >= 2:
        last = log[-1]
        prev = log[-2]
        if last.get("tool") == prev.get("tool") and last.get("args") == prev.get("args"):
            logger.warning("react_loop_detected", tool=last.get("tool"))
            return "fallback"

    return "continue"
