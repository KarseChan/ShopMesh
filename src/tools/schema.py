"""Tool Schema — interface definition and registry for Agent-callable Tools.

Each Tool exposes:
- name: unique identifier (used by ReAct Agent in function calling)
- description: what it does (included in Agent system prompt)
- parameters: JSON Schema defining input format
- return_type: description of return shape
- version: semver string for compatibility

ToolRegistry provides:
- register / get_by_name / get_all
- get_dynamic_tools(): returns ToolDefs for ReAct Agent (excludes internal-only tools)
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field

from src.skills.schema import PermissionLevel


@dataclass
class ToolDef:
    """Definition of an Agent-callable Tool."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    return_type: str = "dict"
    version: str = "1.0.0"
    func: Callable | None = None
    permissions: PermissionLevel = PermissionLevel.READ

    def to_tool_schema(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible function calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Central registry for Agent Tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        """Register a Tool. Overwrites if same name+version."""
        self._tools[tool.name] = tool

    def get_by_name(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def get_all(self) -> list[ToolDef]:
        return list(self._tools.values())

    def get_dynamic_tools(self) -> list[ToolDef]:
        """Return tools intended for ReAct Agent dynamic calling.

        All registered tools are dynamic by default.
        Internal-only tools (e.g. slot_checker) should NOT be registered here;
        they are called internally by higher-level tools.
        """
        return self.get_all()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool schemas for all registered tools."""
        return [t.to_tool_schema() for t in self._tools.values()]

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by name with error handling."""
        tool = self.get_by_name(name)
        if tool is None:
            return {"success": False, "error": f"Tool '{name}' not found"}
        if tool.func is None:
            return {"success": False, "error": f"Tool '{name}' has no implementation"}

        try:
            result = tool.func(**args)
            if inspect.isawaitable(result):
                result = await result
            return {"success": True, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}


# Global singleton
tool_registry = ToolRegistry()
