"""Clarification Parser — parse user's reply to a clarification question.

When the system asks "你想看男装还是女装？", the user might reply "男性的衬衫".
This parser extracts the relevant fields and merges them into the previous entities.
"""

import re

from src.observability.logger import get_logger

logger = get_logger("clarification_parser")

# Gender keywords
_GENDER_MAP = {
    "男": "男", "男性": "男", "男士": "男", "男装": "男", "男款": "男", "man": "男", "male": "男",
    "女": "女", "女性": "女", "女士": "女", "女装": "女", "女款": "女", "woman": "女", "female": "女",
}

# Product type keywords (clothing)
_CLOTHING_TYPES = {
    "衬衫", "T恤", "Polo衫", "卫衣", "外套", "夹克", "西装", "针织衫", "羽绒服",
    "裤子", "裤装", "牛仔裤", "西裤", "休闲裤", "短裤", "裙子", "裙装", "半身裙", "长裙",
    "西装外套", "西装裤", "连衣裙", "针织开衫",
}

# Style/scenario keywords
_STYLE_KEYWORDS = {
    "正式": "正式", "商务": "商务", "休闲": "休闲", "通勤": "通勤",
    "运动": "运动", "约会": "约会", "面试": "面试", "婚礼": "婚礼",
}


def parse_clarification_answer(
    user_input: str,
    pending_fields: list[str],
    previous_entities: dict,
) -> dict:
    """Parse user's clarification answer and merge into previous entities.

    Args:
        user_input: User's reply text (e.g. "男性的衬衫")
        pending_fields: Fields that were asked about (e.g. ["gender", "product_type"])
        previous_entities: The entities from the previous turn

    Returns:
        Merged entities dict with clarification fields filled in
    """
    merged = {**previous_entities}
    parsed_fields = []

    user_lower = user_input.strip()

    # Parse gender
    if "gender" in pending_fields:
        for keyword, gender in _GENDER_MAP.items():
            if keyword in user_lower:
                merged["gender"] = gender
                parsed_fields.append("gender")
                logger.info("clarification_parsed", field="gender", value=gender, source=keyword)
                break

    # Parse product_type
    if "product_type" in pending_fields:
        for pt in _CLOTHING_TYPES:
            if pt in user_lower:
                merged["product_type"] = pt
                parsed_fields.append("product_type")
                logger.info("clarification_parsed", field="product_type", value=pt)
                break

    # Parse style/scenario (bonus: extract from clarification answer)
    for keyword, style in _STYLE_KEYWORDS.items():
        if keyword in user_lower:
            if not merged.get("scenario"):
                merged["scenario"] = style
                logger.info("clarification_parsed", field="scenario", value=style, bonus=True)
            # Add to soft_requirements if not already present
            existing_reqs = merged.get("soft_requirements", [])
            existing_canonicals = [r.get("canonical", "") for r in existing_reqs]
            if style not in existing_canonicals:
                existing_reqs.append({
                    "raw_text": keyword,
                    "canonical": style,
                    "type": "style_preference",
                    "importance": 0.8,
                })
                merged["soft_requirements"] = existing_reqs

    # Remove missing_critical_fields that were resolved
    if parsed_fields:
        missing = merged.get("missing_critical_fields", [])
        merged["missing_critical_fields"] = [f for f in missing if f not in parsed_fields]

        # If all missing fields resolved, clear ambiguous flag
        if not merged["missing_critical_fields"]:
            merged["ambiguous"] = False
            merged["ambiguous_fields"] = []

    logger.info("clarification_merge",
                parsed_fields=parsed_fields,
                gender=merged.get("gender"),
                product_type=merged.get("product_type"),
                remaining_missing=merged.get("missing_critical_fields", []))

    return merged
