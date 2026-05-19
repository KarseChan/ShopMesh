"""Entity Validator — post-extraction rule-based validation.

Responsibilities:
- Check for missing critical fields based on category
- Mark entities["missing_critical_fields"] for downstream use
- Set ambiguous=True if critical fields are missing

Does NOT decide whether to ask the user. That's clarification_decider's job.
"""

from src.observability.logger import get_logger

logger = get_logger("entity_validator")

# Categories that require gender for good recommendations
_GENDER_REQUIRED_CATEGORIES = {"服饰", "运动", "母婴"}

# Categories that require specific product_type
_SPECIFIC_TYPE_REQUIRED_CATEGORIES = {"服饰", "护肤", "运动"}

# Product types that are too vague
_VAGUE_PRODUCT_TYPES = {"衣服", "穿搭", "搭配", "一套", "套装", "服装", "服饰"}

# Product types that are specific enough
_SPECIFIC_PRODUCT_TYPES = {
    "衬衫", "T恤", "Polo衫", "卫衣", "外套", "夹克", "西装", "针织衫", "羽绒服",
    "裤子", "裤装", "牛仔裤", "西裤", "休闲裤", "短裤", "裙子", "裙装", "半身裙", "长裙",
    "双肩包", "背包", "手提包", "斜挎包", "钱包",
    "运动鞋", "皮鞋", "跑步鞋", "休闲鞋", "高跟鞋",
    "面膜", "精华", "面霜", "防晒", "洗面奶",
}


def validate_entities(entities: dict) -> dict:
    """Post-extraction validation: mark missing critical fields.

    Args:
        entities: Entity dict from extract_entities

    Returns:
        Same dict with missing_critical_fields added, ambiguous updated if needed
    """
    category = entities.get("category")
    product_type = entities.get("product_type")
    gender = entities.get("gender")

    missing = []

    # Rule 1: gender required for certain categories
    if category in _GENDER_REQUIRED_CATEGORIES and not gender:
        missing.append("gender")

    # Rule 2: product_type should be specific for certain categories
    if category in _SPECIFIC_TYPE_REQUIRED_CATEGORIES:
        if not product_type or product_type in _VAGUE_PRODUCT_TYPES:
            missing.append("product_type")

    # Rule 3: skin_type required for skincare
    if category == "护肤" and not entities.get("skin_type"):
        missing.append("skin_type")

    if missing:
        # Update missing_critical_fields (append, don't overwrite)
        existing = entities.get("missing_critical_fields", [])
        entities["missing_critical_fields"] = list(set(existing + missing))

        # Mark as ambiguous if not already
        if not entities.get("ambiguous"):
            entities["ambiguous"] = True
            entities["ambiguous_fields"] = list(set(
                entities.get("ambiguous_fields", []) + missing
            ))

        logger.info("validation_failed",
                     category=category,
                     product_type=product_type,
                     gender=gender,
                     missing_fields=missing)
    else:
        entities.setdefault("missing_critical_fields", [])
        logger.info("validation_passed",
                     category=category,
                     product_type=product_type)

    return entities
