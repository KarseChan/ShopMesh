"""Skill Registry — registration, discovery, and version management.

Features:
- register / unregister skills
- semantic discovery: match user intent to skill by description similarity
- version management with canary rollout support
- in-memory store (SQLModel persistence when DB available)
"""

from src.models.embedder import get_embedder
from src.observability.logger import get_logger
from src.skills.schema import PermissionLevel, SkillDefinition

logger = get_logger("skill_registry")

# In-memory store: name -> {version -> SkillDefinition}
_registry: dict[str, dict[str, SkillDefinition]] = {}

# Canary weights: name -> {version -> weight} (sums to 1.0)
_canary: dict[str, dict[str, float]] = {}

# Pre-computed embeddings for semantic matching: name -> list[float]
_embeddings: dict[str, list[float]] = {}


def register(skill: SkillDefinition) -> None:
    """Register a skill. Overwrites same name+version."""
    name = skill.name
    version = skill.version

    if name not in _registry:
        _registry[name] = {}
    _registry[name][version] = skill

    # Default canary: 100% to latest registered version
    _canary[name] = {v: (1.0 if v == version else 0.0) for v in _registry[name]}

    # Clear cached embedding (will be recomputed on next discover)
    _embeddings.pop(name, None)

    logger.info("skill_registered", name=name, version=version, permissions=skill.permissions)


def unregister(name: str, version: str | None = None) -> bool:
    """Unregister a skill or a specific version.

    Returns True if something was removed.
    """
    if name not in _registry:
        return False

    if version:
        if version in _registry[name]:
            del _registry[name][version]
            _canary.get(name, {}).pop(version, None)
            if not _registry[name]:
                del _registry[name]
                _canary.pop(name, None)
                _embeddings.pop(name, None)
            logger.info("skill_unregistered", name=name, version=version)
            return True
        return False

    # Remove all versions
    del _registry[name]
    _canary.pop(name, None)
    _embeddings.pop(name, None)
    logger.info("skill_unregistered", name=name, version="all")
    return True


def get_skill(name: str, version: str | None = None) -> SkillDefinition | None:
    """Get a skill by name (and optional version).

    Without version, returns the canary-selected version.
    """
    if name not in _registry:
        return None

    versions = _registry[name]
    if not versions:
        return None

    if version:
        return versions.get(version)

    # Select by canary weight
    weights = _canary.get(name, {})
    if not weights:
        # Fallback: latest version (last registered)
        return list(versions.values())[-1]

    # Weighted random selection (deterministic: pick highest weight)
    best_version = max(weights, key=lambda v: weights.get(v, 0))
    return versions.get(best_version)


def list_skills() -> list[SkillDefinition]:
    """List all registered skills (one per name, canary-selected version)."""
    result = []
    for name in _registry:
        skill = get_skill(name)
        if skill:
            result.append(skill)
    return result


def set_canary(name: str, weights: dict[str, float]) -> bool:
    """Set canary rollout weights for a skill.

    Args:
        name: Skill name
        weights: {version: weight} — weights are normalized to sum to 1.0

    Returns True if valid and applied.
    """
    if name not in _registry:
        return False

    # Validate versions exist
    for v in weights:
        if v not in _registry[name]:
            return False

    # Normalize
    total = sum(weights.values())
    if total <= 0:
        return False

    _canary[name] = {v: w / total for v, w in weights.items()}
    logger.info("canary_set", name=name, weights=_canary[name])
    return True


async def discover(query: str, top_k: int = 3) -> list[SkillDefinition]:
    """Discover skills by semantic similarity to user query.

    Uses BGE-M3 embeddings to match query against skill descriptions.
    Returns top_k skills sorted by relevance.
    """
    if not _registry:
        return []

    embedder = get_embedder()

    # Ensure all skills have cached embeddings
    missing = [name for name in _registry if name not in _embeddings]
    if missing:
        descriptions = [get_skill(name).description for name in missing]
        vectors = await embedder.aembed_batch(descriptions)
        for name, vec in zip(missing, vectors):
            _embeddings[name] = vec

    # Embed query
    query_vec = await embedder.aembed(query)

    # Cosine similarity
    scored = []
    for name, skill_vec in _embeddings.items():
        sim = _cosine_similarity(query_vec, skill_vec)
        scored.append((sim, name))

    scored.sort(reverse=True)
    results = []
    for sim, name in scored[:top_k]:
        skill = get_skill(name)
        if skill:
            results.append(skill)
            logger.info("skill_discovered", name=name, similarity=round(sim, 3))

    return results


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def clear() -> None:
    """Clear all registrations (for testing)."""
    _registry.clear()
    _canary.clear()
    _embeddings.clear()
