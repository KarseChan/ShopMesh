"""Agent Tools — constraint relaxation and clarification for ReAct Agent.

- constraint_relaxation: relax search constraints when results are insufficient
- ask_clarification: check missing slots and generate clarification questions
"""

from src.agents.clarification_engine import should_clarify
from src.observability.logger import get_logger
from src.tools.schema import ToolDef, tool_registry

logger = get_logger("agent_tools")

# Relaxation priority: remove least impactful constraints first
_RELAXATION_STEPS = [
    ("brand", "去掉品牌限制"),
    ("price_max", "扩大价格上限"),
    ("price_min", "去掉价格下限"),
    ("soft_requirements", "去掉软需求限制"),
    ("scenario", "去掉场景限制"),
    ("preference", "去掉偏好限制"),
    ("product_type", "去掉商品类型限制"),
    ("category", "去掉品类硬过滤"),
]


async def constraint_relaxation(entities: dict, failed_reason: str) -> dict:
    """Relax search constraints step by step, return relaxed entities.

    Strategy (in order):
    1. Remove brand restriction
    2. Expand/remove price range
    3. Remove scenario restriction
    4. Remove preference
    5. Remove product_type
    6. Remove category (last resort)

    Args:
        entities: Current structured entities
        failed_reason: Why relaxation is needed (e.g. "结果过少")

    Returns:
        {"entities": dict, "relaxed": list[str], "steps_remaining": int}
    """
    relaxed = []
    new_entities = {**entities}

    for field, description in _RELAXATION_STEPS:
        current = new_entities.get(field)
        # Check if field has a meaningful value (not None, not empty list)
        if current is not None and current != []:
            # List fields are cleared to empty list; others set to None
            new_entities[field] = [] if isinstance(current, list) else None
            relaxed.append(description)
            logger.info("constraint_relaxed", field=field, reason=failed_reason)
            break  # Relax one step at a time; Agent can call again if needed

    steps_remaining = sum(
        1 for f, _ in _RELAXATION_STEPS
        if new_entities.get(f) is not None and new_entities.get(f) != []
    )

    return {
        "entities": new_entities,
        "relaxed": relaxed,
        "steps_remaining": steps_remaining,
    }


async def ask_clarification(entities: dict, asked_fields: list[str]) -> dict:
    """Check missing slots and generate clarification questions.

    Wraps the existing clarification_engine.should_clarify logic.

    Args:
        entities: Current structured entities
        asked_fields: Fields already asked about (to avoid repetition)

    Returns:
        {"should_ask": bool, "questions": [...], "reason": str}
    """
    # Determine current round from asked_fields length
    round_num = len(asked_fields)

    result = await should_clarify(entities, asked_fields, round_num)

    logger.info("clarification_check",
                should_ask=result["should_ask"],
                reason=result["reason"],
                question_count=len(result["questions"]))

    return {
        "should_ask": result["should_ask"],
        "questions": result["questions"],
        "reason": result["reason"],
    }


# Register constraint_relaxation
tool_registry.register(ToolDef(
    name="constraint_relaxation",
    description="放宽检索约束，返回放宽后的实体。"
                "在检索结果过少时调用，逐步去掉品牌→价格→场景等限制。"
                "重要：拿到返回的 entities 后，必须立即用它重新调用 product_search。",
    parameters={
        "type": "object",
        "properties": {
            "entities": {
                "type": "object",
                "description": "当前结构化实体",
            },
            "failed_reason": {
                "type": "string",
                "description": "需要放宽的原因（如'结果过少'）",
            },
        },
        "required": ["entities", "failed_reason"],
    },
    return_type="dict",
    func=constraint_relaxation,
))

# Register ask_clarification
tool_registry.register(ToolDef(
    name="ask_clarification",
    description="检查缺失槽位并生成追问问题。"
                "返回是否需要追问、问题列表和原因。",
    parameters={
        "type": "object",
        "properties": {
            "entities": {
                "type": "object",
                "description": "当前结构化实体",
            },
            "asked_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "已追问过的字段列表（避免重复追问）",
            },
        },
        "required": ["entities", "asked_fields"],
    },
    return_type="dict",
    func=ask_clarification,
))
