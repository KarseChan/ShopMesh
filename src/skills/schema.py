"""Skill Schema — standardized interface definition for all Skills.

Each Skill exposes:
- name: unique identifier
- description: what it does (used for semantic matching)
- parameters: JSON Schema defining input format
- permissions: read / write / sensitive (controls HITL behavior)
- endpoint: optional API endpoint URL
- version: semver string for compatibility and canary rollout
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PermissionLevel(str, Enum):
    """Skill permission levels controlling human-in-the-loop behavior.

    - read: auto-execute, no confirmation needed
    - write: requires user confirmation before execution
    - sensitive: requires secondary confirmation + verification
    """
    READ = "read"
    WRITE = "write"
    SENSITIVE = "sensitive"


class SkillDefinition(BaseModel):
    """Schema for a registered Skill."""

    name: str = Field(..., description="Unique skill identifier, e.g. 'search_products'")
    description: str = Field(..., description="What this skill does, used for intent matching")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema defining the skill's input parameters",
    )
    permissions: PermissionLevel = Field(
        default=PermissionLevel.READ,
        description="Permission level: read (auto), write (confirm), sensitive (double-confirm)",
    )
    endpoint: str | None = Field(
        default=None,
        description="Optional API endpoint URL for remote skills",
    )
    version: str = Field(
        default="1.0.0",
        description="Semver version string",
    )

    def to_tool_schema(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible function/tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
