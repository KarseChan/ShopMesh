"""Tool Registry Integration — single entry point for all Agent Tools.

Imports all tool modules to trigger self-registration, then re-exports
convenience functions for the ReAct Agent to discover and call tools.
"""

from src.tools.schema import ToolDef, ToolRegistry, tool_registry

# Import all tool modules — each module registers itself on import
import src.tools.product_search      # noqa: F401
import src.tools.multi_query_search  # noqa: F401
import src.tools.product_detail      # noqa: F401
import src.tools.review_tool         # noqa: F401
import src.tools.agent_tools         # noqa: F401


def get_dynamic_tools() -> list[ToolDef]:
    """Return all tools available for ReAct Agent dynamic calling."""
    return tool_registry.get_dynamic_tools()


def get_tool_by_name(name: str) -> ToolDef | None:
    """Look up a tool by name."""
    return tool_registry.get_by_name(name)


def get_all_tool_schemas() -> list[dict]:
    """Return OpenAI-compatible tool schemas for all registered tools."""
    return tool_registry.get_tool_schemas()


__all__ = [
    "ToolDef",
    "ToolRegistry",
    "tool_registry",
    "get_dynamic_tools",
    "get_tool_by_name",
    "get_all_tool_schemas",
]
