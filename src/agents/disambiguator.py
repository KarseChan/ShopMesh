"""Entity Disambiguator — resolve ambiguous entities using context and product data.

Strategy:
1. Build candidate map from product catalog (what could this term mean?)
2. Use conversation context to narrow down
3. If confident, auto-resolve; otherwise return question for user
"""

from src.models.llm_client import get_llm
from src.observability.logger import get_logger
from src.tools.search_tool import load_products

logger = get_logger("disambiguator")

# Known ambiguous terms -> possible meanings
# Built dynamically from product catalog at runtime
_CANDIDATE_CACHE: dict[str, dict] | None = None

# Static fallback for common Chinese ambiguous terms (when catalog has no candidates)
_AMBIGUOUS_TERM_MAP: dict[str, list[dict]] = {
    "苹果": [
        {"value": "数码", "category": "数码", "count": 0, "hint": "Apple iPhone"},
        {"value": "食品", "category": "食品", "count": 0, "hint": "水果"},
    ],
    "小米": [
        {"value": "数码", "category": "数码", "count": 0, "hint": "小米手机/智能家居"},
        {"value": "食品", "category": "食品", "count": 0, "hint": "粮食"},
    ],
    "华为": [
        {"value": "数码", "category": "数码", "count": 0, "hint": "华为手机"},
    ],
    "荣耀": [
        {"value": "数码", "category": "数码", "count": 0, "hint": "荣耀手机"},
        {"value": "游戏", "category": "游戏", "count": 0, "hint": "游戏"},
    ],
}


def _build_candidate_map() -> dict[str, dict]:
    """Build a map of ambiguous terms to their possible meanings from product data.

    Returns:
        {
            "苹果": {
                "category": {"数码": 3, "食品": 0},
                "brand": {"Apple": 3},
                "products": [...]
            }
        }
    """
    global _CANDIDATE_CACHE
    if _CANDIDATE_CACHE is not None:
        return _CANDIDATE_CACHE

    products = load_products()
    term_map: dict[str, dict] = {}

    for p in products:
        name = p["name"]
        brand = p.get("brand", "")
        category = p.get("category", "")

        # Check brand as potential ambiguous term
        if brand:
            key = brand.lower()
            if key not in term_map:
                term_map[key] = {"categories": {}, "brands": {}, "count": 0}
            term_map[key]["categories"][category] = term_map[key]["categories"].get(category, 0) + 1
            term_map[key]["brands"][brand] = term_map[key]["brands"].get(brand, 0) + 1
            term_map[key]["count"] += 1

    # Filter to only terms that span multiple categories (truly ambiguous)
    ambiguous = {}
    for term, info in term_map.items():
        if len(info["categories"]) > 1:
            ambiguous[term] = info

    _CANDIDATE_CACHE = ambiguous
    return ambiguous


def _is_term_ambiguous(term: str) -> dict | None:
    """Check if a term has multiple possible meanings in the product catalog.

    Returns candidate info or None if not ambiguous.
    """
    candidates = _build_candidate_map()
    key = term.lower()
    return candidates.get(key)


async def disambiguate(
    entities: dict,
    conversation_context: list[dict] | None = None,
) -> dict:
    """Attempt to resolve ambiguous entities.

    Args:
        entities: Entity dict from extractor (with ambiguous/ambiguous_fields)
        conversation_context: Previous messages for context

    Returns:
        {
            "resolved": bool,  # True if disambiguation succeeded
            "entities": dict,  # Resolved entities (original if unresolved)
            "question": str | None,  # Question to ask user if unresolved
            "candidates": list[dict],  # Possible meanings for user to choose
        }
    """
    if not entities.get("ambiguous"):
        return {
            "resolved": True,
            "entities": entities,
            "question": None,
            "candidates": [],
        }

    ambiguous_fields = entities.get("ambiguous_fields", [])
    if not ambiguous_fields:
        return {
            "resolved": True,
            "entities": entities,
            "question": None,
            "candidates": [],
        }

    # Try to resolve each ambiguous field
    resolved_entities = {**entities}
    unresolved = []
    raw_query = entities.get("_raw_query", "")

    for field in ambiguous_fields:
        value = entities.get(field)

        result = await _resolve_field(field, value, conversation_context, raw_query=raw_query)
        if result["resolved"]:
            resolved_entities[field] = result["value"]
            logger.info("field_resolved", field=field, original=value, resolved=result["value"])
        else:
            unresolved.append({
                "field": field,
                "value": value,
                "candidates": result["candidates"],
                "question": result["question"],
            })

    if not unresolved:
        resolved_entities["ambiguous"] = False
        resolved_entities["ambiguous_fields"] = []
        return {
            "resolved": True,
            "entities": resolved_entities,
            "question": None,
            "candidates": [],
        }

    # Build combined question for all unresolved fields
    # Store disambiguation question in entities for clarification engine to use
    disambig_questions = [u["question"] for u in unresolved if u["question"]]
    if disambig_questions:
        resolved_entities["_disambiguation_question"] = " ".join(disambig_questions)
    questions = [u["question"] for u in unresolved if u["question"]]
    all_candidates = []
    for u in unresolved:
        all_candidates.extend(u["candidates"])

    return {
        "resolved": False,
        "entities": resolved_entities,
        "question": " ".join(questions) if questions else None,
        "candidates": all_candidates,
    }


async def _resolve_field(
    field: str,
    value: str,
    context: list[dict] | None,
    raw_query: str = "",
) -> dict:
    """Try to resolve a single ambiguous field.

    Args:
        field: The field name (e.g. "category", "brand")
        value: The extracted value (may be None if LLM detected ambiguity but couldn't assign)
        context: Conversation context
        raw_query: Original user input (used when value is None)

    Returns:
        {"resolved": bool, "value": any, "candidates": list, "question": str|None}
    """
    # When value is None, use raw_query to find candidates
    search_term = value or raw_query
    if not search_term:
        return {"resolved": False, "value": value, "candidates": [], "question": None}

    if value is None:
        logger.info("field_value_none", field=field, using_raw_query=raw_query)

    # Strategy 1: Check product catalog for candidates
    candidates = _get_catalog_candidates(field, search_term)

    if not candidates:
        return {"resolved": False, "value": value, "candidates": [], "question": None}

    if len(candidates) == 1:
        # Only one meaning in catalog, auto-resolve
        only = candidates[0]
        return {"resolved": True, "value": only["value"], "candidates": candidates, "question": None}

    # Strategy 2: Use conversation context
    if context:
        context_resolved = _resolve_from_context(field, search_term, candidates, context)
        if context_resolved:
            return {"resolved": True, "value": context_resolved, "candidates": candidates, "question": None}

    # Strategy 3: Use LLM to disambiguate
    llm_resolved = await _resolve_via_llm(field, search_term, candidates, context)
    if llm_resolved:
        return {"resolved": True, "value": llm_resolved, "candidates": candidates, "question": None}

    # Fallback: ask user
    question = _build_question(field, search_term, candidates)
    return {"resolved": False, "value": value, "candidates": candidates, "question": question}


def _get_catalog_candidates(field: str, value: str) -> list[dict]:
    """Get possible meanings for a value from the product catalog.

    Returns list of {"value": resolved_value, "category": str, "count": int}
    """
    products = load_products()
    candidates = []
    seen = set()

    for p in products:
        if field == "brand":
            # Check if value matches brand or name
            if value.lower() in p.get("brand", "").lower() or value in p["name"]:
                key = (p["category"], p["brand"])
                if key not in seen:
                    seen.add(key)
                    candidates.append({
                        "value": p["brand"],
                        "category": p["category"],
                        "count": 1,
                    })
                else:
                    for c in candidates:
                        if c["value"] == p["brand"] and c["category"] == p["category"]:
                            c["count"] += 1
        elif field == "category":
            # Check if value matches category or product name
            if value in p.get("category", "") or value in p["name"]:
                cat = p["category"]
                if cat not in seen:
                    seen.add(cat)
                    candidates.append({"value": cat, "category": cat, "count": 1})
                else:
                    for c in candidates:
                        if c["value"] == cat:
                            c["count"] += 1

    # Fallback: check static ambiguous term map (exact or substring match)
    if not candidates:
        if value in _AMBIGUOUS_TERM_MAP:
            candidates = list(_AMBIGUOUS_TERM_MAP[value])
            logger.info("using_static_fallback", term=value, candidates=len(candidates))
        else:
            # Substring match: "我要苹果" contains "苹果"
            for term, term_candidates in _AMBIGUOUS_TERM_MAP.items():
                if term in value:
                    candidates = list(term_candidates)
                    logger.info("using_static_fallback", term=term, candidates=len(candidates))
                    break

    return candidates


def _resolve_from_context(
    field: str,
    value: str,
    candidates: list[dict],
    context: list[dict],
) -> str | None:
    """Try to resolve using conversation context.

    Looks for hints in previous messages about which meaning is intended.
    """
    # Combine all context text
    context_text = " ".join(msg.get("content", "") for msg in context[-5:])

    if field == "brand":
        # If context mentions a category, prefer the candidate in that category
        for c in candidates:
            if c["category"] in context_text:
                return c["value"]

    elif field == "category":
        # If context mentions a brand, prefer the category that brand belongs to
        for c in candidates:
            for p in load_products():
                if p.get("category") == c["value"] and p.get("brand", "") in context_text:
                    return c["value"]

    return None


async def _resolve_via_llm(
    field: str,
    value: str,
    candidates: list[dict],
    context: list[dict] | None,
) -> str | None:
    """Use LLM to disambiguate based on context."""
    llm = get_llm()

    context_text = ""
    if context:
        recent = context[-3:]
        context_text = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in recent)

    candidate_desc = "\n".join(
        f"- {c['value']} ({c['category']}, {c['count']}个商品)"
        for c in candidates
    )

    prompt = (
        f"用户说了'{value}'，这是一个有歧义的词。\n"
        f"可能的含义：\n{candidate_desc}\n"
    )
    if context_text:
        prompt += f"对话上下文：\n{context_text}\n"
    prompt += (
        "根据上下文判断最可能的含义，只输出对应的值（如品牌名或品类名）。"
        "如果无法判断，输出 null。只输出结果，不要其他文字。"
    )

    messages = [
        {"role": "system", "content": "你是消歧助手。根据上下文判断有歧义的词的含义。"},
        {"role": "user", "content": prompt},
    ]

    try:
        result = await llm.chat_json(messages)
        if not result:
            return None
        resolved = result.get("result") or result.get("value")
        if resolved and resolved != "null":
            # Verify it's a valid candidate
            valid_values = {c["value"] for c in candidates}
            if resolved in valid_values:
                return resolved
    except Exception as e:
        logger.error("llm_disambiguation_failed", error=str(e))

    return None


def _build_question(field: str, value: str, candidates: list[dict]) -> str:
    """Build a disambiguation question with options for the user."""
    if field == "brand":
        options = "还是".join(
            f"{c['category']}的{c['value']}" for c in candidates[:3]
        )
        return f"你说的'{value}'是指{options}？"
    elif field == "category":
        # Use hint if available for clearer options
        parts = []
        for c in candidates[:3]:
            hint = c.get("hint", "")
            if hint:
                parts.append(f"{c['value']}（{hint}）")
            else:
                parts.append(c["value"])
        options = "还是".join(parts)
        return f"你说的'{value}'是指{options}？"
    else:
        options = "还是".join(c["value"] for c in candidates[:3])
        return f"'{value}'有多种含义，你是指{options}？"
