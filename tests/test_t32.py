"""T3.2 Skill Registry tests."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.skills.schema import PermissionLevel, SkillDefinition
from src.skills.registry import (
    register, unregister, get_skill, list_skills,
    set_canary, discover, clear,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure clean registry for each test."""
    clear()
    yield
    clear()


def _make_skill(name="search", desc="Search products", version="1.0.0", perm="read"):
    return SkillDefinition(
        name=name, description=desc, version=version,
        permissions=PermissionLevel(perm),
    )


# === Register / Unregister ===

def test_register_and_get():
    skill = _make_skill()
    register(skill)
    got = get_skill("search")
    assert got is not None
    assert got.name == "search"
    assert got.version == "1.0.0"


def test_register_overwrites_same_version():
    register(_make_skill(desc="old"))
    register(_make_skill(desc="new"))
    got = get_skill("search")
    assert got.description == "new"


def test_register_multiple_versions():
    register(_make_skill(version="1.0.0"))
    register(_make_skill(version="2.0.0"))
    # Default canary: 100% to latest
    got = get_skill("search")
    assert got.version == "2.0.0"


def test_get_specific_version():
    register(_make_skill(version="1.0.0"))
    register(_make_skill(version="2.0.0"))
    got = get_skill("search", version="1.0.0")
    assert got.version == "1.0.0"


def test_get_nonexistent():
    assert get_skill("nope") is None


def test_unregister_version():
    register(_make_skill(version="1.0.0"))
    register(_make_skill(version="2.0.0"))
    assert unregister("search", "1.0.0") is True
    assert get_skill("search", "1.0.0") is None
    assert get_skill("search", "2.0.0") is not None


def test_unregister_all():
    register(_make_skill())
    assert unregister("search") is True
    assert get_skill("search") is None


def test_unregister_nonexistent():
    assert unregister("nope") is False


# === List ===

def test_list_skills():
    register(_make_skill("search", "Search"))
    register(_make_skill("order", "Place order"))
    skills = list_skills()
    names = {s.name for s in skills}
    assert names == {"search", "order"}


def test_list_skills_empty():
    assert list_skills() == []


# === Canary ===

def test_set_canary():
    register(_make_skill(version="1.0.0"))
    register(_make_skill(version="2.0.0"))
    set_canary("search", {"1.0.0": 0.7, "2.0.0": 0.3})
    # Highest weight wins
    got = get_skill("search")
    assert got.version == "1.0.0"


def test_set_canary_invalid_version():
    register(_make_skill(version="1.0.0"))
    assert set_canary("search", {"9.0.0": 1.0}) is False


def test_set_canary_nonexistent_skill():
    assert set_canary("nope", {"1.0.0": 1.0}) is False


def test_set_canary_normalizes():
    register(_make_skill(version="1.0.0"))
    register(_make_skill(version="2.0.0"))
    set_canary("search", {"1.0.0": 70, "2.0.0": 30})
    got = get_skill("search")
    assert got.version == "1.0.0"


# === Discover (mocked embeddings) ===

@pytest.mark.asyncio
async def test_discover_finds_match():
    register(_make_skill("search", "Search for products by keyword"))
    register(_make_skill("order", "Place an order for a product"))

    with patch("src.skills.registry.get_embedder") as mock_get_embedder:
        mock_embedder = MagicMock()
        # search desc embedding, order desc embedding, query embedding
        mock_embedder.aembed_batch = AsyncMock(return_value=[
            [1.0, 0.0, 0.0],  # search
            [0.0, 1.0, 0.0],  # order
        ])
        mock_embedder.aembed = AsyncMock(return_value=[0.9, 0.1, 0.0])  # query similar to search
        mock_get_embedder.return_value = mock_embedder

        results = await discover("find me a product")
        assert len(results) >= 1
        assert results[0].name == "search"


@pytest.mark.asyncio
async def test_discover_empty_registry():
    results = await discover("anything")
    assert results == []


@pytest.mark.asyncio
async def test_discover_top_k():
    for i in range(5):
        register(_make_skill(f"skill_{i}", f"Description {i}"))

    with patch("src.skills.registry.get_embedder") as mock_get_embedder:
        mock_embedder = MagicMock()
        mock_embedder.aembed_batch = AsyncMock(return_value=[
            [1.0, 0.0] for _ in range(5)
        ])
        mock_embedder.aembed = AsyncMock(return_value=[1.0, 0.0])
        mock_get_embedder.return_value = mock_embedder

        results = await discover("query", top_k=2)
        assert len(results) == 2
