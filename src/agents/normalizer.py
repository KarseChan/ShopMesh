"""Normalizer — canonicalize soft_requirements for stable matching.

Responsibilities:
1. Extract canonical keyword from raw text (remove modifiers)
2. Expand terms via synonym table
3. Ensure consistent format for ranker consumption

Input format (from LLM):
  {"text": "适合通勤", "type": "scenario", "importance": 0.8}
  or
  {"raw_text": "适合通勤", "canonical": "通勤", "type": "scenario", "importance": 0.8}

Output format (for ranker):
  {"raw_text": "适合通勤", "canonical": "通勤", "terms": ["通勤", "商务", ...], "type": "scenario", "importance": 0.8}
"""

import re
from pathlib import Path

import yaml

from src.observability.logger import get_logger

logger = get_logger("normalizer")

# Modifiers to strip from raw_text when extracting canonical
_MODIFIERS = [
    "适合", "适用于", "用于", "想要", "推荐", "几款", "一些", "比较",
    "非常", "特别", "需要", "找", "看看", "有没有", "可以", "能够",
    "最好", "希望", "如果能", "顺便", "帮我", "给我",
]

# Synonym table (loaded once)
_SYNONYMS: dict[str, list[str]] | None = None


def _load_synonyms() -> dict[str, list[str]]:
    """Load synonym table from configs/ranking/synonyms.yaml."""
    global _SYNONYMS
    if _SYNONYMS is not None:
        return _SYNONYMS

    synonyms_path = Path(__file__).resolve().parent.parent.parent / "configs" / "ranking" / "synonyms.yaml"
    try:
        with open(synonyms_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _SYNONYMS = data.get("synonyms", {})
    except Exception:
        _SYNONYMS = {}
    return _SYNONYMS


def _strip_modifiers(text: str) -> str:
    """Remove common modifiers from text to extract core keyword."""
    result = text.strip()
    for mod in _MODIFIERS:
        if result.startswith(mod):
            result = result[len(mod):]
        if result.endswith(mod):
            result = result[:-len(mod)]
    return result.strip()


def _expand_terms(canonical: str) -> list[str]:
    """Expand canonical keyword to related terms via synonym table.

    Looks up both directions:
    1. canonical as key → get its synonyms
    2. canonical as value → find its key and siblings
    """
    synonyms = _load_synonyms()
    terms = [canonical]

    # 1. Direct lookup: canonical is a key
    if canonical in synonyms:
        terms.extend(synonyms[canonical])

    # 2. Reverse lookup: canonical is a value → find key and siblings
    for key, values in synonyms.items():
        if canonical in values:
            terms.append(key)
            terms.extend(v for v in values if v != canonical)
            break

    # Deduplicate preserving order
    return list(dict.fromkeys(terms))


def normalize_soft_requirements(requirements: list[dict]) -> list[dict]:
    """Normalize soft_requirements to ensure canonical + terms for each req.

    Handles both old format (text only) and new format (raw_text + canonical).

    Args:
        requirements: List of soft_requirements from entity extractor

    Returns:
        Normalized list with raw_text, canonical, terms, type, importance
    """
    if not requirements:
        return []

    normalized = []
    for req in requirements:
        # Extract raw_text from either format
        raw_text = req.get("raw_text") or req.get("text", "")
        if not raw_text:
            continue

        # Extract canonical: prefer explicit canonical, else strip modifiers
        canonical = req.get("canonical")
        if not canonical:
            canonical = _strip_modifiers(raw_text)
        if not canonical:
            canonical = raw_text  # Fallback to raw if stripping yielded nothing

        # Expand terms
        terms = _expand_terms(canonical)

        # Preserve other fields
        entry = {
            "raw_text": raw_text,
            "canonical": canonical,
            "terms": terms,
            "type": req.get("type", "unknown"),
            "importance": req.get("importance", 0.7),
        }
        normalized.append(entry)

    logger.info("requirements_normalized",
                input_count=len(requirements),
                output_count=len(normalized),
                samples=[(r["raw_text"], r["canonical"], len(r["terms"])) for r in normalized[:3]])

    return normalized
