"""Scenario Filter — map user scenarios to allowed/excluded product categories.

When the user specifies a scenario (e.g., "送女朋友生日礼物"), this module
determines which product categories are suitable and which should be excluded.
"""

from src.observability.logger import get_logger

logger = get_logger("scenario_filter")

# Scenario → allowed categories (whitelist)
# If a scenario is matched, ONLY these categories are kept.
SCENARIO_CATEGORY_MAP = {
    # 礼物场景
    "gift": {
        "keywords": ["礼物", "送", "礼品", "惊喜", "表白", "纪念日"],
        "allowed": ["护肤", "数码", "服饰", "运动"],
        "excluded": ["奶茶", "食品", "家居"],
    },
    "birthday_gift": {
        "keywords": ["生日礼物", "生日"],
        "allowed": ["护肤", "数码", "服饰", "运动"],
        "excluded": ["奶茶", "食品", "家居"],
    },
    "valentine_gift": {
        "keywords": ["情人节", "七夕"],
        "allowed": ["护肤", "数码", "服饰"],
        "excluded": ["奶茶", "食品", "家居", "运动"],
    },
}


def get_scenario_categories(entities: dict) -> dict | None:
    """Determine allowed/excluded categories based on scenario.

    Returns:
        {"allowed": set, "excluded": set} or None if no scenario match
    """
    scenario = entities.get("scenario", "")
    if not scenario:
        return None

    for rule_name, rule in SCENARIO_CATEGORY_MAP.items():
        for keyword in rule["keywords"]:
            if keyword in scenario:
                allowed = set(rule.get("allowed", []))
                excluded = set(rule.get("excluded", []))
                logger.info("scenario_matched",
                            rule=rule_name, keyword=keyword,
                            allowed=list(allowed), excluded=list(excluded))
                return {"allowed": allowed, "excluded": excluded}

    return None


def filter_by_scenario(products: list[dict], entities: dict) -> list[dict]:
    """Filter products by scenario suitability.

    Products in excluded categories are removed.
    If allowed categories are defined, products not in allowed are also removed.
    """
    scenario_rules = get_scenario_categories(entities)
    if not scenario_rules:
        return products

    allowed = scenario_rules.get("allowed")
    excluded = scenario_rules.get("excluded", set())

    filtered = []
    removed = []
    for p in products:
        category = p.get("category", "")
        if category in excluded:
            removed.append((p.get("product_id", ""), category, "excluded"))
            continue
        if allowed and category not in allowed:
            removed.append((p.get("product_id", ""), category, "not_in_allowed"))
            continue
        filtered.append(p)

    if removed:
        logger.info("scenario_filter_applied",
                     removed_count=len(removed),
                     remaining=len(filtered),
                     removed_ids=[r[0] for r in removed])

    return filtered
