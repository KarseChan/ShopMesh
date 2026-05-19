"""ReAct Loop Node — core reasoning loop for the shopping Agent.

Implements Thought → Action → Observation cycle:
1. Build messages with preprocessed context + tool call history
2. LLM decides next action (tool call or final answer)
3. Execute tool and return observation, or return final_response
"""

from src.agents.react_prompt import build_react_messages
from src.graph.tool_executor import execute_tool
from src.models.llm_client import get_llm
from src.observability.logger import get_logger
from src.tools.registry import get_all_tool_schemas

logger = get_logger("react_node")

# Fields that the Agent may omit but are needed for ranking
_INJECTABLE_FIELDS = ("soft_requirements", "hard_constraints", "gender")


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

    # LLM decides next action
    response = await llm.chat(messages, tools=tool_schemas)

    tool_calls = response.get("tool_calls")
    if tool_calls:
        # Execute first tool call (we only support one per iteration)
        tc = tool_calls[0]
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        import json
        try:
            tool_args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            tool_args = {}

        logger.info("react_action", tool=tool_name, iteration=state.get("iteration", 0))

        # Defensive: inject preprocessed entity fields into product_search if Agent omitted them
        if tool_name == "product_search" and "entities" in tool_args:
            _inject_entity_fields(tool_args["entities"], state.get("entities", {}))

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

        return {
            "final_response": content,
            "iteration": state.get("iteration", 0) + 1,
        }


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
