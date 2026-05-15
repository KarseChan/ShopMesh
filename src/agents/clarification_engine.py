"""Clarification Engine — ask clarifying questions based on information gaps.

Strategy: priority = missing_degree x discrimination_power
Stop conditions: candidates < 20 or rounds >= 3
"""

from src.observability.logger import get_logger
from src.tools.search_tool import load_products

logger = get_logger("clarification_engine")

# Fields that can be asked about, with their question templates
QUESTION_TEMPLATES = {
    "category": {
        "question": "你想找什么类型的商品？比如护肤、数码、服饰、食品等",
        "priority_weight": 1.0,
    },
    "brand": {
        "question": "有偏好的品牌吗？",
        "priority_weight": 0.7,
    },
    "price_max": {
        "question": "你的预算大概是多少？",
        "priority_weight": 0.9,
    },
    "price_min": {
        "question": "你期望的最低价位是多少？",
        "priority_weight": 0.5,
    },
    "scenario": {
        "question": "是自己用还是送人？什么场景下使用？",
        "priority_weight": 0.6,
    },
    "quantity": {
        "question": "你大概需要多少件？",
        "priority_weight": 0.4,
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
    else:
        distinct = 1

    # Normalize: more distinct values = higher discrimination
    return min(distinct / 5.0, 1.0)


def _select_question(entities: dict, asked_fields: list[str]) -> str | None:
    """Select the highest-priority question to ask next."""
    candidates = _count_candidates(entities)
    best_field = None
    best_priority = -1.0

    for field, template in QUESTION_TEMPLATES.items():
        # Skip if already has value or already asked
        if entities.get(field) is not None:
            continue
        if field in asked_fields:
            continue

        # Skip price_min if price_max is already set
        if field == "price_min" and entities.get("price_max"):
            continue

        # Calculate priority
        missing_degree = 1.0  # field is missing
        disc_power = _get_discrimination_power(entities, field)
        priority = missing_degree * disc_power * template["priority_weight"]

        if priority > best_priority:
            best_priority = priority
            best_field = field

    if best_field is None:
        return None
    return QUESTION_TEMPLATES[best_field]["question"]


async def should_clarify(entities: dict, asked_fields: list[str], round_num: int) -> dict:
    """Decide whether to ask a clarifying question.

    Returns:
        {
            "should_ask": bool,
            "question": str or None,
            "reason": str,
            "candidates": int,
        }
    """
    candidates = _count_candidates(entities)

    # Stop condition 1: enough info, few candidates
    if candidates <= 20:
        logger.info("clarification_stop", reason="candidates_le_20", candidates=candidates)
        return {
            "should_ask": False,
            "question": None,
            "reason": "candidates_le_20",
            "candidates": candidates,
        }

    # Stop condition 2: max rounds reached
    if round_num >= 3:
        logger.info("clarification_stop", reason="max_rounds", round_num=round_num)
        return {
            "should_ask": False,
            "question": None,
            "reason": "max_rounds",
            "candidates": candidates,
        }

    # Stop condition 3: ambiguous entities need disambiguation first
    if entities.get("ambiguous"):
        disambig_q = _build_disambiguation_question(entities)
        if disambig_q:
            logger.info("clarification_ask", question=disambig_q, reason="disambiguation")
            return {
                "should_ask": True,
                "question": disambig_q,
                "reason": "disambiguation",
                "candidates": candidates,
            }

    # Select best question
    question = _select_question(entities, asked_fields)
    if question is None:
        logger.info("clarification_stop", reason="no_more_questions")
        return {
            "should_ask": False,
            "question": None,
            "reason": "no_more_questions",
            "candidates": candidates,
        }

    logger.info("clarification_ask", question=question, candidates=candidates)
    return {
        "should_ask": True,
        "question": question,
        "reason": "missing_info",
        "candidates": candidates,
    }


def _build_disambiguation_question(entities: dict) -> str | None:
    """Build a disambiguation question for ambiguous fields."""
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
