"""Filter Builder — convert entity dict to Qdrant Filter conditions.

Transforms the output of entity extraction into Qdrant-native filters
for one-step hybrid retrieval (Payload pre-filtering during HNSW traversal).
"""

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, Range


def build_filter(entities: dict) -> Filter | None:
    """Build a Qdrant Filter from extracted entities.

    Args:
        entities: Dict from entity extractor with keys like
                  category, brand, price_min, price_max, etc.

    Returns:
        Qdrant Filter object, or None if no filterable conditions.
    """
    conditions = []

    # Category filter (exact match)
    category = entities.get("category")
    if category:
        conditions.append(FieldCondition(
            key="category", match=MatchValue(value=category)
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
