"""Filter Builder — convert entity dict to Qdrant Filter conditions.

Transforms the output of entity extraction into Qdrant-native filters
for one-step hybrid retrieval (Payload pre-filtering during HNSW traversal).
"""

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, MatchText, Range

from src.tools.search_tool import load_products

# Cache for available categories
_CATEGORIES_CACHE: list[str] | None = None

# Broad category → prefix mapping (entity extractor outputs these)
_BROAD_CATEGORY_MAP: dict[str, list[str]] = {
    "服饰": ["男装/", "女装/"],
    "衣服": ["男装/", "女装/"],
    "服装": ["男装/", "女装/"],
    "男装": ["男装/"],
    "女装": ["女装/"],
}

# Product type → actual categories (for precise matching)
_PRODUCT_TYPE_MAP: dict[str, list[str]] = {
    "衬衫": ["男装/上装", "女装/上装"],
    "T恤": ["男装/上装", "女装/上装"],
    "Polo衫": ["男装/上装"],
    "背心": ["男装/上装", "女装/上装"],
    "卫衣": ["男装/上装", "女装/上装"],
    "外套": ["男装/上装", "女装/上装"],
    "夹克": ["男装/上装"],
    "西装": ["女装/上装"],
    "针织衫": ["男装/上装", "女装/上装"],
    "羽绒服": ["男装/上装", "女装/上装"],
    "裤子": ["男装/下装", "女装/下装"],
    "裤装": ["男装/下装", "女装/下装"],
    "牛仔裤": ["男装/下装", "女装/下装"],
    "西裤": ["男装/下装", "女装/下装"],
    "休闲裤": ["男装/下装", "女装/下装"],
    "短裤": ["男装/下装"],
    "裙子": ["女装/下装"],
    "裙装": ["女装/下装"],
    "半身裙": ["女装/下装"],
    "长裙": ["女装/下装"],
}


def _get_all_categories() -> list[str]:
    """Get all unique categories from product data."""
    global _CATEGORIES_CACHE
    if _CATEGORIES_CACHE is None:
        products = load_products()
        _CATEGORIES_CACHE = list({p["category"] for p in products if p.get("category")})
    return _CATEGORIES_CACHE


def _expand_category(category: str | None, product_type: str | None = None) -> list[str]:
    """Expand a category to all matching full categories.

    Expansion priority:
    1. product_type → direct mapping (e.g., "衬衫" → ["男装/上装/T恤衬衫", ...])
    2. category → broad prefix map (e.g., "服饰" → ["男装/", "女装/"])
    3. category → prefix match on actual categories
    4. category → exact match
    5. Fallback to original

    Examples:
        ("服饰", "衬衫") → ["男装/上装/T恤衬衫", "女装/上装/衬衫外套"]
        ("服饰", None) → ["男装/上装/T恤衬衫", "男装/下装/裤装", ...]
        ("男装", None) → ["男装/上装/T恤衬衫", "男装/下装/裤装", ...]
        (None, "衬衫") → ["男装/上装/T恤衬衫", "女装/上装/衬衫外套"]
    """
    all_cats = _get_all_categories()
    candidates = []

    # 1. product_type direct mapping (most precise, highest priority)
    if product_type and product_type in _PRODUCT_TYPE_MAP:
        candidates.extend(_PRODUCT_TYPE_MAP[product_type])

    # 2. Broad category map (only if product_type didn't narrow it down)
    if not candidates and category and category in _BROAD_CATEGORY_MAP:
        prefixes = _BROAD_CATEGORY_MAP[category]
        for c in all_cats:
            if any(c.startswith(p) for p in prefixes):
                candidates.append(c)

    # 3. Prefix match on actual categories
    if not candidates and category:
        matched = [c for c in all_cats if c.startswith(category)]
        if matched:
            candidates.extend(matched)

    # 4. Exact match
    if category and not candidates and category in all_cats:
        candidates.append(category)

    # Deduplicate preserving order
    return list(dict.fromkeys(candidates)) if candidates else ([category] if category else [])


def build_filter(entities: dict) -> Filter | None:
    """Build a Qdrant Filter from extracted entities.

    Args:
        entities: Dict from entity extractor with keys like
                  category, brand, price_min, price_max, etc.

    Returns:
        Qdrant Filter object, or None if no filterable conditions.
    """
    conditions = []

    # Category filter: expand broad categories + product_type to actual categories
    # e.g., ("服饰", "衬衫") → MatchAny(["男装/上装/T恤衬衫", "女装/上装/衬衫外套"])
    category = entities.get("category")
    product_type = entities.get("product_type")
    if category or product_type:
        expanded = _expand_category(category, product_type)
        if len(expanded) > 1:
            conditions.append(FieldCondition(
                key="category", match=MatchAny(any=expanded)
            ))
        elif expanded:
            conditions.append(FieldCondition(
                key="category", match=MatchValue(value=expanded[0])
            ))

    # Brand filter (exact match on brand field)
    brand = entities.get("brand")
    if brand:
        conditions.append(FieldCondition(
            key="brand", match=MatchValue(value=brand)
        ))

    # Price range filter
    price_min = entities.get("price_min")
    price_max = entities.get("price_max")
    if price_min is not None or price_max is not None:
        range_kwargs = {}
        if price_min is not None:
            range_kwargs["gte"] = float(price_min)
        if price_max is not None:
            range_kwargs["lte"] = float(price_max)
        conditions.append(FieldCondition(
            key="price", range=Range(**range_kwargs)
        ))

    # Platform filter (if specified)
    platform = entities.get("platform_id")
    if platform:
        conditions.append(FieldCondition(
            key="platform_id", match=MatchValue(value=platform)
        ))

    # Multi-value filter (e.g., multiple categories)
    categories = entities.get("categories")
    if categories and isinstance(categories, list):
        conditions.append(FieldCondition(
            key="category", match=MatchAny(any=categories)
        ))

    if not conditions:
        return None

    return Filter(must=conditions)


def build_filter_from_keywords(keywords: list[str]) -> Filter | None:
    """Build a filter that matches any of the given keyword values in name.

    Note: This is a simple inclusion check, not full-text search.
    For semantic search, use vector similarity instead.
    """
    if not keywords:
        return None

    # For keyword matching, we rely on vector search, not payload filter
    # This function exists for cases where keyword = category/brand
    return None
