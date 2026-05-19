"""Clarification Router — route user input when pending_clarification exists.

Determines whether the user's next message is:
- clarification_answer: answering the pending question (e.g. "男性的衬衫")
- task_switch_full: completely new task (e.g. "算了，帮我看看平板电脑")
- task_switch_partial: same scenario, different product (e.g. "换成皮鞋吧")
- unclear: ambiguous, needs re-ask or assumption

Design: rule-first + LLM fallback. Rules handle clear cases deterministically;
LLM handles ambiguous inputs like "还是成熟一点吧".
"""

import re

from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("clarification_router")

# --- Keyword dicts ---

# Strong cancel cues: when combined with a new category → task_switch_full
_STRONG_CANCEL_CUES = [
    "不用了", "不要了", "算了", "取消", "不找了", "不想要了",
    "不要这个", "算了不要了", "不想买了",
]

# Weak switch cues: when combined with a new category → task_switch_partial
_WEAK_SWITCH_CUES = [
    "换成", "改成", "换个", "看看", "帮我看看", "帮我看",
    "我想买", "我要买", "给我找", "有没有",
]

# Gender keywords (reuse from clarification_parser pattern)
_GENDER_MAP = {
    "男": "男", "男性": "男", "男士": "男", "男装": "男", "男款": "男",
    "女": "女", "女性": "女", "女士": "女", "女装": "女", "女款": "女",
}

# Product type keywords — broad set for category detection
# Maps keyword → (canonical_type, broad_category)
_PRODUCT_TYPE_MAP = {
    # 服饰
    "衬衫": ("衬衫", "服饰"), "T恤": ("T恤", "服饰"), "Polo衫": ("Polo衫", "服饰"),
    "卫衣": ("卫衣", "服饰"), "外套": ("外套", "服饰"), "夹克": ("夹克", "服饰"),
    "西装": ("西装", "服饰"), "西装外套": ("西装外套", "服饰"),
    "针织衫": ("针织衫", "服饰"), "羽绒服": ("羽绒服", "服饰"),
    "裤子": ("裤子", "服饰"), "裤装": ("裤装", "服饰"), "牛仔裤": ("牛仔裤", "服饰"),
    "西裤": ("西裤", "服饰"), "休闲裤": ("休闲裤", "服饰"), "短裤": ("短裤", "服饰"),
    "裙子": ("裙子", "服饰"), "裙装": ("裙装", "服饰"), "半身裙": ("半身裙", "服饰"),
    "长裙": ("长裙", "服饰"), "连衣裙": ("连衣裙", "服饰"),
    "衣服": ("衣服", "服饰"), "穿搭": ("穿搭", "服饰"), "正装": ("正装", "服饰"),
    # 鞋靴
    "鞋": ("鞋", "鞋靴"), "鞋子": ("鞋", "鞋靴"), "皮鞋": ("皮鞋", "鞋靴"),
    "运动鞋": ("运动鞋", "鞋靴"), "高跟鞋": ("高跟鞋", "鞋靴"), "靴子": ("靴子", "鞋靴"),
    "凉鞋": ("凉鞋", "鞋靴"), "拖鞋": ("拖鞋", "鞋靴"),
    # 箱包
    "包": ("包", "箱包"), "背包": ("背包", "箱包"), "手提包": ("手提包", "箱包"),
    "钱包": ("钱包", "箱包"), "行李箱": ("行李箱", "箱包"),
    # 护肤/美妆
    "面膜": ("面膜", "护肤"), "精华": ("精华", "护肤"), "面霜": ("面霜", "护肤"),
    "防晒": ("防晒", "护肤"), "口红": ("口红", "护肤"), "粉底": ("粉底", "护肤"),
    "护肤品": ("护肤品", "护肤"), "化妆品": ("化妆品", "护肤"),
    # 数码
    "手机": ("手机", "数码"), "电脑": ("电脑", "数码"), "平板": ("平板", "数码"),
    "平板电脑": ("平板电脑", "数码"), "耳机": ("耳机", "数码"), "手表": ("手表", "数码"),
    "笔记本": ("笔记本", "数码"),
    # 食品/饮品
    "奶茶": ("奶茶", "饮品"), "咖啡": ("咖啡", "饮品"), "零食": ("零食", "食品"),
    # 运动
    "瑜伽垫": ("瑜伽垫", "运动"), "哑铃": ("哑铃", "运动"), "跑步机": ("跑步机", "运动"),
}

# Broad categories that are NOT clothing (for cross-category switch detection)
_NON_CLOTHING_CATEGORIES = {"鞋靴", "箱包", "护肤", "数码", "饮品", "食品", "运动", "家居", "母婴"}


def _has_strong_cancel_cue(text: str) -> bool:
    """Check if text contains a strong cancel/abort cue."""
    return any(cue in text for cue in _STRONG_CANCEL_CUES)


def _has_weak_switch_cue(text: str) -> bool:
    """Check if text contains a weak switch cue."""
    return any(cue in text for cue in _WEAK_SWITCH_CUES)


def _extract_product_types(text: str) -> list[tuple[str, str]]:
    """Extract product types from text. Returns [(canonical_type, broad_category), ...]."""
    found = []
    for keyword, (canonical, category) in _PRODUCT_TYPE_MAP.items():
        if keyword in text:
            found.append((canonical, category))
    return found


def _contains_pending_field_values(text: str, pending_fields: list[str]) -> dict:
    """Check if text contains values for pending fields. Returns parsed fields."""
    parsed = {}

    if "gender" in pending_fields:
        for keyword, gender in _GENDER_MAP.items():
            if keyword in text:
                parsed["gender"] = gender
                break

    if "product_type" in pending_fields:
        product_types = _extract_product_types(text)
        if product_types:
            # Use first match
            parsed["product_type"] = product_types[0][0]

    return parsed


def _is_related_to_pending(product_types: list[tuple[str, str]], previous_entities: dict) -> bool:
    """Check if extracted product types are related to the pending task."""
    prev_category = previous_entities.get("category", "")
    prev_product_type = previous_entities.get("product_type", "")

    for canonical, broad_category in product_types:
        # Same broad category as previous entities
        if broad_category == "服饰" and prev_category in ("服饰", "运动"):
            return True
        if canonical == prev_product_type:
            return True
    return False


async def _llm_route(user_input: str, pending_fields: list[str],
                     previous_entities: dict, question_type: str) -> dict:
    """LLM fallback for ambiguous cases."""
    SYSTEM_PROMPT = """你是一个路由分类器。系统正在等待用户回答一个追问，需要判断用户的回复类型。

输入信息：
- 用户回复文本
- 系统追问的字段列表
- 上一轮的实体信息
- 追问类型

判断规则：
1. clarification_answer: 用户在回答追问（补充了缺失字段的值）
2. task_switch_full: 用户完全放弃当前任务，转向新的商品/品类
3. task_switch_partial: 用户在同场景下换了一个商品类型
4. unclear: 无法判断，既没有回答追问也没有明确切换

注意：
- "我想买男士衬衫" 这种含追问字段值的，是 clarification_answer
- "帮我看男款衬衫" 也是 clarification_answer
- 只有同时出现取消词+新品类，才是 task_switch
- "随便吧"、"都可以" 是 unclear

只输出 JSON，不要其他文字。"""

    user_msg = f"""用户回复: "{user_input}"
追问字段: {pending_fields}
追问类型: {question_type}
上一轮实体: category={previous_entities.get("category")}, product_type={previous_entities.get("product_type")}, scenario={previous_entities.get("scenario")}

请判断路由类型并解析字段。"""

    llm = get_llm()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    try:
        result = await llm.chat_json(messages)
        route = result.get("route", "unclear")
        if route not in ("clarification_answer", "task_switch_full", "task_switch_partial", "unclear"):
            route = "unclear"
        return {
            "route": route,
            "confidence": result.get("confidence", 0.7),
            "parsed_fields": result.get("parsed_fields", {}),
            "new_input": result.get("new_input"),
            "reason": result.get("reason", "LLM 判断"),
        }
    except Exception as e:
        logger.error("clarification_router_llm_error", error=str(e))
        return {
            "route": "unclear",
            "confidence": 0.0,
            "parsed_fields": {},
            "new_input": None,
            "reason": f"LLM 调用失败: {str(e)}",
        }


async def route_clarification(
    user_input: str,
    pending_fields: list[str],
    previous_entities: dict,
    question_type: str,
) -> dict:
    """Route user input when pending_clarification exists.

    Priority:
    1. Strong cancel cue + new category → task_switch_full
    2. Contains pending field values → clarification_answer
    3. Weak switch cue + new category → task_switch_partial
    4. LLM fallback

    Args:
        user_input: User's reply text
        pending_fields: Fields that were asked about (e.g. ["gender", "product_type"])
        previous_entities: Entities from the previous turn
        question_type: Type of clarification question asked

    Returns:
        {route, confidence, parsed_fields, new_input, reason}
    """
    text = user_input.strip()

    # --- Layer 1: Rule-based ---

    # 1. Strong cancel + new category → task_switch_full
    if _has_strong_cancel_cue(text):
        product_types = _extract_product_types(text)
        if product_types and not _is_related_to_pending(product_types, previous_entities):
            # Cross-category switch with strong cancel
            logger.info("clarification_routed",
                        route="task_switch_full", method="rule",
                        cancel_cue=True, new_categories=[pt[1] for pt in product_types])
            return {
                "route": "task_switch_full",
                "confidence": 0.95,
                "parsed_fields": {},
                "new_input": text,
                "reason": f"强取消词 + 跨品类切换到 {product_types[0][1]}",
            }
        elif product_types and _is_related_to_pending(product_types, previous_entities):
            # Same category but strong cancel → still full switch
            logger.info("clarification_routed",
                        route="task_switch_full", method="rule",
                        cancel_cue=True, same_category=True)
            return {
                "route": "task_switch_full",
                "confidence": 0.90,
                "parsed_fields": {},
                "new_input": text,
                "reason": f"强取消词 + 品类切换",
            }
        elif not product_types:
            # Strong cancel but no new category specified → task_switch_full, let Path A handle
            logger.info("clarification_routed",
                        route="task_switch_full", method="rule",
                        cancel_cue=True, no_new_category=True)
            return {
                "route": "task_switch_full",
                "confidence": 0.85,
                "parsed_fields": {},
                "new_input": text,
                "reason": "强取消词，用户放弃当前任务",
            }

    # 2. Contains pending field values → clarification_answer
    parsed = _contains_pending_field_values(text, pending_fields)
    if parsed:
        logger.info("clarification_routed",
                    route="clarification_answer", method="rule",
                    parsed_fields=parsed)
        return {
            "route": "clarification_answer",
            "confidence": 0.95,
            "parsed_fields": parsed,
            "new_input": None,
            "reason": f"用户回答了 pending_fields: {list(parsed.keys())}",
        }

    # 3. Weak switch cue + new category → task_switch_partial
    if _has_weak_switch_cue(text):
        product_types = _extract_product_types(text)
        if product_types and not _is_related_to_pending(product_types, previous_entities):
            logger.info("clarification_routed",
                        route="task_switch_partial", method="rule",
                        switch_cue=True, new_categories=[pt[1] for pt in product_types])
            return {
                "route": "task_switch_partial",
                "confidence": 0.88,
                "parsed_fields": {"product_type": product_types[0][0]},
                "new_input": text,
                "reason": f"弱切换词 + 同场景品类切换到 {product_types[0][0]}",
            }
        elif product_types and _is_related_to_pending(product_types, previous_entities):
            # Same category with switch cue — might be clarification answer with extra words
            # e.g. "我想买男士衬衫" has "我想买" but is answering gender+product_type
            parsed = _contains_pending_field_values(text, pending_fields)
            if parsed:
                logger.info("clarification_routed",
                            route="clarification_answer", method="rule",
                            parsed_fields=parsed, weak_switch_but_answer=True)
                return {
                    "route": "clarification_answer",
                    "confidence": 0.90,
                    "parsed_fields": parsed,
                    "new_input": None,
                    "reason": f"含弱切换词但实际回答了 pending_fields: {list(parsed.keys())}",
                }

    # --- Layer 2: LLM fallback ---
    logger.info("clarification_routed", route="llm_fallback", method="llm")
    result = await _llm_route(user_input, pending_fields, previous_entities, question_type)
    logger.info("clarification_routed",
                route=result["route"], method="llm",
                confidence=result["confidence"],
                reason=result["reason"])
    return result
