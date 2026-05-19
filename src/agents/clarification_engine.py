"""Clarification Engine — ask clarifying questions based on information gaps.

Strategy: priority = missing_degree x discrimination_power
Stop conditions: rounds >= 3 or no more high-priority questions
"""

from src.observability.logger import get_logger
from src.tools.search_tool import load_products

logger = get_logger("clarification_engine")

# Fields that can be asked about, with their question templates and clickable options
QUESTION_TEMPLATES = {
    "category": {
        "question": "你想找什么类型的商品？",
        "options": ["护肤", "数码", "服饰", "食品", "家居", "母婴"],
        "priority_weight": 1.0,
        "applies_to": None,  # all categories
    },
    "brand": {
        "question": "有偏好的品牌吗？",
        "options": [],
        "priority_weight": 0.7,
        "applies_to": None,
    },
    "price_max": {
        "question": "你的预算大概是多少？",
        "options": ["100以内", "100-300", "300-500", "500-1000", "1000以上"],
        "priority_weight": 0.9,
        "applies_to": None,
    },
    "price_min": {
        "question": "你期望的最低价位是多少？",
        "options": [],
        "priority_weight": 0.5,
        "applies_to": None,
    },
    "scenario": {
        "question": "是自己用还是送人？",
        "options": ["自己用", "送人", "公司采购"],
        "priority_weight": 0.6,
        "applies_to": None,
    },
    "quantity": {
        "question": "你大概需要多少件？",
        "options": ["1件", "2-3件", "5件以上"],
        "priority_weight": 0.4,
        "applies_to": None,
    },
    "skin_type": {
        "question": "你的肤质是？",
        "options": ["油皮/混油", "干皮/混干", "敏感肌", "不太清楚"],
        "priority_weight": 0.95,
        "applies_to": ["护肤"],
    },
    "concerns": {
        "question": "主要想解决什么问题？",
        "options": ["补水保湿", "控油祛痘", "抗老紧致", "提亮肤色"],
        "priority_weight": 0.9,
        "applies_to": ["护肤"],
    },
}


def _count_candidates(entities: dict) -> int:
    """Count how many products match current entity filters."""
    products = load_products()
    count = 0
    for p in products:
        if entities.get("category") and p["category"] != entities["category"]:
            continue
        if entities.get("brand") and entities["brand"] not in p["name"]:
            continue
        if entities.get("price_max") and p["price"] > entities["price_max"]:
            continue
        if entities.get("price_min") and p["price"] < entities["price_min"]:
            continue
        count += 1
    return count


def _count_with_field(entities: dict, field: str, value) -> int:
    """Count candidates if a specific field were filled."""
    test = {**entities, field: value}
    return _count_candidates(test)


def _get_discrimination_power(entities: dict, field: str) -> float:
    """Estimate how much asking about this field would narrow results.

    Higher value = more useful to ask about.
    Based on how many distinct values exist for this field in matching products.
    """
    products = load_products()
    matching = []
    for p in products:
        if entities.get("category") and p["category"] != entities["category"]:
            continue
        if entities.get("brand") and entities["brand"] not in p["name"]:
            continue
        if entities.get("price_max") and p["price"] > entities["price_max"]:
            continue
        if entities.get("price_min") and p["price"] < entities["price_min"]:
            continue
        matching.append(p)

    if not matching:
        return 0.0

    # Count distinct values for the field
    if field == "category":
        distinct = len(set(p["category"] for p in matching))
    elif field == "brand":
        distinct = len(set(p["brand"] for p in matching))
    elif field in ("price_min", "price_max"):
        # Price discrimination: how many price ranges
        prices = [p["price"] for p in matching]
        if len(prices) < 2:
            return 0.0
        distinct = len(set(int(p / 50) for p in prices))  # 50-yuan buckets
    elif field == "scenario":
        distinct = 3  # default estimate
    elif field == "quantity":
        distinct = 2  # less discriminating
    elif field in ("skin_type", "concerns"):
        distinct = 4  # high discrimination for personalized questions
    else:
        distinct = 1

    # Normalize: more distinct values = higher discrimination
    return min(distinct / 5.0, 1.0)


def _select_questions(entities: dict, asked_fields: list[str], max_questions: int = 3) -> list[dict]:
    """Select up to max_questions high-priority questions to ask.

    Returns list of {"field": str, "question": str, "options": list[str]}.
    """
    candidates = []

    for field, template in QUESTION_TEMPLATES.items():
        # Skip if already has value or already asked
        if entities.get(field) is not None:
            continue
        if field in asked_fields:
            continue

        # Skip price_min if price_max is already set
        if field == "price_min" and entities.get("price_max"):
            continue

        # Skip if question doesn't apply to current category
        applies_to = template.get("applies_to")
        if applies_to and entities.get("category") not in applies_to:
            continue

        # Calculate priority
        missing_degree = 1.0
        disc_power = _get_discrimination_power(entities, field)
        priority = missing_degree * disc_power * template["priority_weight"]

        candidates.append({
            "field": field,
            "question": template["question"],
            "options": template.get("options", []),
            "priority": priority,
        })

    # Sort by priority descending, take top N
    candidates.sort(key=lambda x: x["priority"], reverse=True)
    return [
        {"field": c["field"], "question": c["question"], "options": c["options"]}
        for c in candidates[:max_questions]
        if c["priority"] > 0.1  # skip very low priority
    ]


async def should_clarify(entities: dict, asked_fields: list[str], round_num: int) -> dict:
    """Decide whether to ask clarifying questions.

    Returns:
        {
            "should_ask": bool,
            "questions": list[{"field": str, "question": str, "options": list[str]}],
            "reason": str,
            "candidates": int,
        }
    """
    candidates = _count_candidates(entities)

    # Stop condition 1: max rounds reached
    if round_num >= 3:
        logger.info("clarification_stop", reason="max_rounds", round_num=round_num)
        return {
            "should_ask": False,
            "questions": [],
            "reason": "max_rounds",
            "candidates": candidates,
        }

    # Stop condition 2: ambiguous entities need disambiguation first
    if entities.get("ambiguous"):
        disambig_q = _build_disambiguation_question(entities)
        if disambig_q:
            logger.info("clarification_ask", question=disambig_q, reason="disambiguation")
            return {
                "should_ask": True,
                "questions": [{"field": None, "question": disambig_q, "options": []}],
                "reason": "disambiguation",
                "candidates": candidates,
            }

    # Select questions (batch up to 3)
    questions = _select_questions(entities, asked_fields, max_questions=3)
    if not questions:
        logger.info("clarification_stop", reason="no_more_questions")
        return {
            "should_ask": False,
            "questions": [],
            "reason": "no_more_questions",
            "candidates": candidates,
        }

    logger.info("clarification_ask", questions=[q["question"] for q in questions],
                candidates=candidates)
    return {
        "should_ask": True,
        "questions": questions,
        "reason": "missing_info",
        "candidates": candidates,
    }


def _build_disambiguation_question(entities: dict) -> str | None:
    """Build a disambiguation question for ambiguous fields."""
    # Use disambiguator's specific question if available
    disambig_q = entities.get("_disambiguation_question")
    if disambig_q:
        return disambig_q

    ambiguous_fields = entities.get("ambiguous_fields", [])
    if not ambiguous_fields:
        return None

    examples = []
    for field in ambiguous_fields:
        if field == "category":
            examples.append(f"你说的'{entities.get('category', '')}'是指哪个品类？")
        elif field == "brand":
            examples.append(f"你说的品牌是电子品牌还是食品品牌？")
        else:
            examples.append(f"关于{field}，能再具体一些吗？")

    return " ".join(examples) if examples else None
