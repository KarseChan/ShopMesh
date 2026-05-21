"""Filter Builder — convert entity dict to Qdrant Filter conditions.

Transforms the output of entity extraction into Qdrant-native filters
for one-step hybrid retrieval (Payload pre-filtering during HNSW traversal).
"""

from math import sqrt

from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, MatchText, Range

from src.tools.search_tool import load_products

# Cache for available categories
_CATEGORIES_CACHE: list[str] | None = None

# Auto-discovered product_type → categories mapping
_PT_TO_CAT_CACHE: dict[str, list[str]] | None = None

# All known searchable type terms (for vector fallback)
_KNOWN_TYPES_CACHE: list[str] | None = None

# Pre-computed embeddings for known types: (keys, vectors)
_CATEGORY_EMBED_CACHE: tuple[list[str], list[list[float]]] | None = None

# Broad category → prefix mapping (entity extractor outputs these)
_BROAD_CATEGORY_MAP: dict[str, list[str]] = {
    "服饰": ["男装/", "女装/"],
    "衣服": ["男装/", "女装/"],
    "服装": ["男装/", "女装/"],
    "男装": ["男装/"],
    "女装": ["女装/"],
    "箱包": ["箱包/"],
    "背包": ["箱包/"],
    "双肩包": ["箱包/"],
    "鞋": ["鞋靴/"],
    "鞋靴": ["鞋靴/"],
    "鞋子": ["鞋靴/"],
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
    "西装": ["男装/上装", "女装/上装"],
    "双肩包": ["箱包/双肩包"],
    "背包": ["箱包/双肩包"],
    "包": ["箱包/"],
    "通勤包": ["箱包/"],
    "手提包": ["箱包/"],
    "单肩包": ["箱包/"],
    "斜挎包": ["箱包/"],
    "公文包": ["箱包/"],
    "电脑包": ["箱包/"],
    "行李箱": ["箱包/"],
    "拉杆箱": ["箱包/"],
    "运动鞋": ["鞋靴/运动鞋"],
    "皮鞋": ["鞋靴/皮鞋"],
    "跑步鞋": ["鞋靴/运动鞋"],
    "休闲鞋": ["鞋靴/运动鞋", "鞋靴/皮鞋"],
}


def _get_all_categories() -> list[str]:
    """Get all unique categories from product data."""
    global _CATEGORIES_CACHE
    if _CATEGORIES_CACHE is None:
        products = load_products()
        _CATEGORIES_CACHE = list({p["category"] for p in products if p.get("category")})
    return _CATEGORIES_CACHE


def _get_product_type_to_categories() -> dict[str, list[str]]:
    """Auto-discover product_type → categories mapping from database.

    Builds a reverse index from product_type to all category paths that contain it.
    E.g., {"双肩包": ["箱包/双肩包"], "T恤": ["男装/上装", "女装/上装"], ...}
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


def _get_all_known_types() -> list[str]:
    """Get all searchable type terms from database + manual maps.

    Combines: database product_types, _BROAD_CATEGORY_MAP keys, _PRODUCT_TYPE_MAP keys.
    """
    global _KNOWN_TYPES_CACHE
    if _KNOWN_TYPES_CACHE is None:
        db_types = set(_get_product_type_to_categories().keys())
        broad_keys = set(_BROAD_CATEGORY_MAP.keys())
        pt_keys = set(_PRODUCT_TYPE_MAP.keys())
        _KNOWN_TYPES_CACHE = list(db_types | broad_keys | pt_keys)
    return _KNOWN_TYPES_CACHE


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _build_category_embedding_index() -> tuple[list[str], list[list[float]]]:
    """Pre-compute embeddings for all known type terms.

    Returns (keys, vectors) tuple. Cached in module-level _CATEGORY_EMBED_CACHE.
    """
    global _CATEGORY_EMBED_CACHE
    if _CATEGORY_EMBED_CACHE is not None:
        return _CATEGORY_EMBED_CACHE

    from src.models.embedder import get_embedder
    embedder = get_embedder()
    keys = _get_all_known_types()
    vectors = await embedder.aembed_batch(keys)
    _CATEGORY_EMBED_CACHE = (keys, vectors)
    return _CATEGORY_EMBED_CACHE


async def _semantic_type_match(product_type: str, threshold: float = 0.5) -> str | None:
    """Find the closest known type via vector similarity.

    Returns the best-matching key if similarity > threshold, else None.
    """
    from src.models.embedder import get_embedder
    embedder = get_embedder()

    keys, vectors = await _build_category_embedding_index()
    query_vec = await embedder.aembed(product_type)

    best_key = None
    best_sim = 0.0
    for key, vec in zip(keys, vectors):
        sim = _cosine_similarity(query_vec, vec)
        if sim > best_sim:
            best_sim = sim
            best_key = key

    if best_sim >= threshold:
        return best_key
    return None


def _expand_prefixes(candidates: list[str], all_cats: list[str]) -> list[str]:
    """Expand prefix entries (ending with '/') to actual categories from database.

    E.g., ["箱包/", "男装/上装"] with all_cats=["箱包/双肩包", "男装/上装"]
    → ["箱包/双肩包", "男装/上装"]
    """
    expanded = []
    for c in candidates:
        if c.endswith("/"):
            for cat in all_cats:
                if cat.startswith(c):
                    expanded.append(cat)
        else:
            expanded.append(c)
    return expanded


async def _expand_category(
    category: str | None,
    product_type: str | None = None,
    gender: str | None = None,
) -> list[str]:
    """Expand a category to all matching full categories.

    Three-layer matching:
    1. Manual maps (_PRODUCT_TYPE_MAP, _BROAD_CATEGORY_MAP) — fast path
    2. Database auto-discovery (_get_product_type_to_categories) — zero maintenance
    3. Vector semantic fallback (_semantic_type_match) — handles unknown terms

    Gender filter: if gender is "男", filter to only "男装/..." categories;
    if "女", filter to only "女装/..." categories.
    """
    all_cats = _get_all_categories()
    pt_to_cat = _get_product_type_to_categories()
    candidates = []

    # Gender prefix filter
    gender_prefix = None
    if gender == "男":
        gender_prefix = "男装/"
    elif gender == "女":
        gender_prefix = "女装/"

    # --- Layer 1: Manual maps (fast path) ---

    # 1a. product_type → _PRODUCT_TYPE_MAP (most precise)
    if product_type and product_type in _PRODUCT_TYPE_MAP:
        candidates.extend(_expand_prefixes(_PRODUCT_TYPE_MAP[product_type], all_cats))

    # 1b. category → _BROAD_CATEGORY_MAP
    if not candidates and category and category in _BROAD_CATEGORY_MAP:
        prefixes = _BROAD_CATEGORY_MAP[category]
        for c in all_cats:
            if any(c.startswith(p) for p in prefixes):
                candidates.append(c)

    # --- Layer 2: Database auto-discovery ---

    # 2a. product_type → database reverse index
    if not candidates and product_type and product_type in pt_to_cat:
        candidates.extend(pt_to_cat[product_type])

    # 2b. category → prefix match on actual categories
    if not candidates and category:
        matched = [c for c in all_cats if c.startswith(category)]
        if matched:
            candidates.extend(matched)

    # 2c. category → exact match
    if category and not candidates and category in all_cats:
        candidates.append(category)

    # --- Layer 3: Vector semantic fallback ---

    if not candidates and product_type:
        matched_type = await _semantic_type_match(product_type)
        if matched_type:
            # Try manual map first
            if matched_type in _PRODUCT_TYPE_MAP:
                candidates.extend(_expand_prefixes(_PRODUCT_TYPE_MAP[matched_type], all_cats))
            # Then database index
            elif matched_type in pt_to_cat:
                candidates.extend(pt_to_cat[matched_type])
            # Then broad category map
            elif matched_type in _BROAD_CATEGORY_MAP:
                prefixes = _BROAD_CATEGORY_MAP[matched_type]
                for c in all_cats:
                    if any(c.startswith(p) for p in prefixes):
                        candidates.append(c)

    # Apply gender filter: narrow to matching gender prefix
    if gender_prefix and candidates:
        gender_filtered = [c for c in candidates if c.startswith(gender_prefix)]
        if gender_filtered:
            candidates = gender_filtered

    # Deduplicate preserving order
    return list(dict.fromkeys(candidates)) if candidates else ([category] if category else [])


async def build_filter(entities: dict) -> Filter | None:
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
    gender = entities.get("gender")
    if category or product_type:
        expanded = await _expand_category(category, product_type, gender)
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
