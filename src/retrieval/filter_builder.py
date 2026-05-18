"""Filter Builder — convert entity dict to Qdrant Filter conditions.

Transforms the output of entity extraction into Qdrant-native filters
for one-step hybrid retrieval (Payload pre-filtering during HNSW traversal).
"""

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, MatchText, Range

from src.tools.search_tool import load_products

# Cache for available categories
_CATEGORIES_CACHE: list[str] | None = None


def _get_all_categories() -> list[str]:
    """Get all unique categories from product data."""
    global _CATEGORIES_CACHE
    if _CATEGORIES_CACHE is None:
        products = load_products()
        _CATEGORIES_CACHE = list({p["category"] for p in products if p.get("category")})
    return _CATEGORIES_CACHE


def _expand_category(category: str) -> list[str]:
    """Expand a category prefix to all matching full categories.

    Examples:
        "服饰" → ["男装/上装/T恤衬衫", "男装/下装/裤装", "女装/上装/衬衫外套", ...]
        "男装" → ["男装/上装/T恤衬衫", "男装/下装/裤装", ...]
        "男装/上装" → ["男装/上装/T恤衬衫", "男装/上装/外套", ...]
        "男装/上装/T恤衬衫" → ["男装/上装/T恤衬衫"] (exact)
    """
    all_cats = _get_all_categories()
    # Exact match first
    if category in all_cats:
        return [category]
    # Prefix match
    matched = [c for c in all_cats if c.startswith(category)]
    return matched if matched else [category]  # fallback to original if no match


def build_filter(entities: dict) -> Filter | None:
    """Build a Qdrant Filter from extracted entities.

    Args:
        entities: Dict from entity extractor with keys like
                  category, brand, price_min, price_max, etc.

    Returns:
        Qdrant Filter object, or None if no filterable conditions.
    """
    conditions = []

    # Category filter: prefix match for hierarchical categories
    # e.g., "男装" matches "男装/上装/T恤衬衫", "男装/下装/裤装"
    # e.g., "男装/上装" matches "男装/上装/T恤衬衫"
    category = entities.get("category")
    if category:
        # Use MatchText for prefix-like matching on category field
        # MatchText does full-text match, but we can use MatchAny with expanded categories
        expanded = _expand_category(category)
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
