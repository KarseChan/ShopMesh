"""T3.1 Skill Schema tests."""

import pytest
from pydantic import ValidationError

from src.skills.schema import SkillDefinition, PermissionLevel


# === PermissionLevel ===

def test_permission_levels():
    assert PermissionLevel.READ == "read"
    assert PermissionLevel.WRITE == "write"
    assert PermissionLevel.SENSITIVE == "sensitive"


def test_permission_from_string():
    assert PermissionLevel("read") == PermissionLevel.READ
    assert PermissionLevel("write") == PermissionLevel.WRITE


# === SkillDefinition ===

def test_skill_minimal():
    skill = SkillDefinition(name="test", description="A test skill")
    assert skill.name == "test"
    assert skill.permissions == PermissionLevel.READ
    assert skill.version == "1.0.0"
    assert skill.endpoint is None
    assert skill.parameters == {}


def test_skill_full():
    params = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_price": {"type": "number", "description": "Max price filter"},
        },
        "required": ["query"],
    }
    skill = SkillDefinition(
        name="search_products",
        description="Search for products by keyword",
        parameters=params,
        permissions=PermissionLevel.READ,
        endpoint="/api/skills/search",
        version="2.1.0",
    )
    assert skill.name == "search_products"
    assert skill.parameters["required"] == ["query"]
    assert skill.permissions == PermissionLevel.READ
    assert skill.version == "2.1.0"


def test_skill_write_permission():
    skill = SkillDefinition(
        name="place_order",
        description="Place an order",
        permissions=PermissionLevel.WRITE,
    )
    assert skill.permissions == PermissionLevel.WRITE


def test_skill_sensitive_permission():
    skill = SkillDefinition(
        name="refund",
        description="Process a refund",
        permissions=PermissionLevel.SENSITIVE,
    )
    assert skill.permissions == PermissionLevel.SENSITIVE


def test_skill_missing_name():
    with pytest.raises(ValidationError):
        SkillDefinition(description="no name")


def test_skill_missing_description():
    with pytest.raises(ValidationError):
        SkillDefinition(name="no_desc")


# === to_tool_schema ===

def test_to_tool_schema():
    skill = SkillDefinition(
        name="search",
        description="Search products",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )
    schema = skill.to_tool_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "search"
    assert schema["function"]["description"] == "Search products"
    assert "query" in schema["function"]["parameters"]["properties"]


def test_to_tool_schema_empty_params():
    skill = SkillDefinition(name="ping", description="Health check")
    schema = skill.to_tool_schema()
    assert schema["function"]["parameters"] == {}
