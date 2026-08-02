"""Filter Builder — convert entity dict to Qdrant Filter conditions.

Transforms the output of entity extraction into Qdrant-native filters
for one-step hybrid retrieval (Payload pre-filtering during HNSW traversal).

Schema note: the live catalog (data/mock_products_5k.json) uses a FLAT taxonomy
— `category` and `product_type` are independent payload fields (e.g. 服饰 / 衬衫).
An earlier nested taxonomy (男装/上装 …) and its keyword→category expansion
(`_expand_category` + hand-maintained maps) were dead against this schema and
have been removed; `build_filter` hard-filters on the flat fields directly.
"""

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, Range

from src.agents.scenario_filter import get_scenario_categories
from src.tools.search_tool import load_products

# Cache for available categories
_CATEGORIES_CACHE: list[str] | None = None

# Cache for available product_types
_PRODUCT_TYPES_CACHE: set[str] | None = None

# Auto-discovered product_type → categories mapping (used by rule_extractor)
_PT_TO_CAT_CACHE: dict[str, list[str]] | None = None


def _get_all_categories() -> list[str]:
    """Get all unique categories from product data."""
    global _CATEGORIES_CACHE
    if _CATEGORIES_CACHE is None:
        products = load_products()
        _CATEGORIES_CACHE = list({p["category"] for p in products if p.get("category")})
    return _CATEGORIES_CACHE


def _get_all_product_types() -> set[str]:
    """All product_type values present in the catalog (flat schema)."""
    global _PRODUCT_TYPES_CACHE
    if _PRODUCT_TYPES_CACHE is None:
        products = load_products()
        _PRODUCT_TYPES_CACHE = {p["product_type"] for p in products if p.get("product_type")}
    return _PRODUCT_TYPES_CACHE


def _get_product_type_to_categories() -> dict[str, list[str]]:
    """Auto-discover product_type → categories mapping from the catalog.

    Reverse index from product_type to the category paths that contain it.
    Consumed by src/agents/rule_extractor.py for fast entity extraction.
    """
    global _PT_TO_CAT_CACHE
    if _PT_TO_CAT_CACHE is None:
        products = load_products()
        mapping: dict[str, set[str]] = {}
        for p in products:
            pt = p.get("product_type")
            cat = p.get("category")
            if pt and cat:
                mapping.setdefault(pt, set()).add(cat)
        _PT_TO_CAT_CACHE = {k: list(v) for k, v in mapping.items()}
    return _PT_TO_CAT_CACHE


async def build_filter(entities: dict) -> Filter | None:
    """Build a Qdrant Filter from extracted entities.

    Args:
        entities: Dict from entity extractor with keys like
                  category, brand, price_min, price_max, etc.

    Returns:
        Qdrant Filter object, or None if no filterable conditions.
    """
    conditions = []

    # Flat schema (data/mock_products_5k.json): `category` and `product_type` are
    # separate payload fields. Hard-filter only on values that actually exist in
    # the catalog — an unknown/near-miss value (e.g. "跑鞋" vs "跑步鞋") is left to
    # vector search instead of producing a category filter that matches nothing.
    category = entities.get("category")
    product_type = entities.get("product_type")
    category_filtered = False

    if product_type and product_type in _get_all_product_types():
        conditions.append(FieldCondition(
            key="product_type", match=MatchValue(value=product_type)
        ))

    if category and category in set(_get_all_categories()):
        conditions.append(FieldCondition(
            key="category", match=MatchValue(value=category)
        ))
        category_filtered = True

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
        category_filtered = True

    # Scenario category whitelist (e.g. 送礼 → only 护肤/数码/服饰/运动).
    # Push the whitelist INTO the Qdrant pre-filter so HNSW only traverses
    # gift-appropriate categories. Without this, an anchor-less query like
    # "送女朋友的生日礼物" recalls a top-K dominated by 奶茶, and the scenario
    # post-filter then empties the result. Skip when the user already gave an
    # explicit category — respect their choice over the scenario heuristic.
    if not category_filtered:
        scenario_cats = get_scenario_categories(entities)
        if scenario_cats and scenario_cats.get("allowed"):
            allowed = [c for c in scenario_cats["allowed"]
                       if c in set(_get_all_categories())]
            if allowed:
                conditions.append(FieldCondition(
                    key="category", match=MatchAny(any=allowed)
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
