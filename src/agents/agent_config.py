"""Agent Config — central registry for specialized agent configurations.

Each agent has:
- name: unique identifier
- tools: list of tool names this agent can call
- max_iterations: ReAct loop limit
- response_type: frontend rendering hint

Config is read from config.yaml `agents` section.
"""

from dataclasses import dataclass, field

from src.config import config
from src.tools.registry import get_tools_by_names, get_tool_schemas_by_names


@dataclass
class AgentConfig:
    """Configuration for a specialized agent."""
    name: str
    tools: list[str] = field(default_factory=list)
    max_iterations: int = 5
    response_type: str = "recommendation_cards"


# user_goal → agent_name mapping
# place_order is handled as a hardcoded branch in agent_router, not as an agent
_ORDER_PLACEHOLDER = "__order_hardcoded__"

INTENT_TO_AGENT: dict[str, str] = {
    "recommend_product": "search_recommend_agent",
    "find_product": "search_recommend_agent",
    "compare_products": "detail_compare_agent",
    "view_detail": "detail_compare_agent",
    "place_order": _ORDER_PLACEHOLDER,
}

_DEFAULT_AGENT = "search_recommend_agent"


def _load_agent_configs() -> dict[str, AgentConfig]:
    """Load agent configs from config.yaml `agents` section."""
    agents_cfg = config.get("agents", {})
    configs = {}
    for name, cfg in agents_cfg.items():
        configs[name] = AgentConfig(
            name=name,
            tools=cfg.get("tools", []),
            max_iterations=cfg.get("max_iterations", 5),
            response_type=cfg.get("response_type", "recommendation_cards"),
        )
    # Ensure all mapped agents exist even if not in config (skip order placeholder)
    for agent_name in INTENT_TO_AGENT.values():
        if agent_name != _ORDER_PLACEHOLDER and agent_name not in configs:
            configs[agent_name] = AgentConfig(name=agent_name)
    return configs


_AGENT_CONFIGS: dict[str, AgentConfig] | None = None


def get_agent_configs() -> dict[str, AgentConfig]:
    """Get all agent configs (lazy-loaded singleton)."""
    global _AGENT_CONFIGS
    if _AGENT_CONFIGS is None:
        _AGENT_CONFIGS = _load_agent_configs()
    return _AGENT_CONFIGS


def get_agent_config(agent_name: str) -> AgentConfig:
    """Get config for a specific agent. Falls back to search_recommend_agent."""
    configs = get_agent_configs()
    return configs.get(agent_name, configs.get(_DEFAULT_AGENT, AgentConfig(name=agent_name)))


def get_tools_for_agent(agent_name: str) -> list:
    """Get ToolDef list for the agent's assigned tools."""
    cfg = get_agent_config(agent_name)
    return get_tools_by_names(cfg.tools)


def get_tool_schemas_for_agent(agent_name: str) -> list[dict]:
    """Get OpenAI tool schemas for the agent's assigned tools."""
    cfg = get_agent_config(agent_name)
    return get_tool_schemas_by_names(cfg.tools)


def resolve_agent(user_goal: str) -> str:
    """Resolve user_goal to agent name. Deterministic, no LLM."""
    return INTENT_TO_AGENT.get(user_goal, _DEFAULT_AGENT)


def resolve_agents(user_goals: list[str]) -> list[str]:
    """Resolve multiple user_goals to agent names. Deduplicated, order preserved.

    Skips the order placeholder — place_order is handled by agent_router directly.
    """
    seen = set()
    agents = []
    for goal in user_goals:
        name = INTENT_TO_AGENT.get(goal, _DEFAULT_AGENT)
        if name == _ORDER_PLACEHOLDER:
            continue
        if name not in seen:
            seen.add(name)
            agents.append(name)
    return agents


def is_order_intent(user_goals: list[str]) -> bool:
    """Check if any user goal is a place_order intent."""
    return any(g == "place_order" for g in user_goals)


def merge_agent_configs(agent_names: list[str]) -> AgentConfig:
    """Merge multiple agent configs: union tools, max of max_iterations.

    For single agent, returns its own config.
    For multiple agents, picks the first agent's name/prompt context,
    unions all tools, and takes the max max_iterations.
    response_type follows the primary (first) agent.
    """
    if len(agent_names) == 1:
        return get_agent_config(agent_names[0])

    configs = [get_agent_config(n) for n in agent_names]

    # Union tools, preserve order
    seen_tools = set()
    merged_tools = []
    for cfg in configs:
        for t in cfg.tools:
            if t not in seen_tools:
                seen_tools.add(t)
                merged_tools.append(t)

    return AgentConfig(
        name=configs[0].name,
        tools=merged_tools,
        max_iterations=max(c.max_iterations for c in configs),
        response_type=configs[0].response_type,
    )
