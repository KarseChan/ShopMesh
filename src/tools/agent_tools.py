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
    """Decide whether to ask clarifying questions with structured spec.

    Strategy table:
    - 语义歧义严重 (e.g. "苹果") → must ask
    - 服饰类 gender + product_type 都缺失 → prefer ask
    - 只缺 gender，product_type 明确 → can assume or light ask
    - 用户说"直接推荐" → don't ask, assume
    - 已追问过一次，用户没回答 → don't ask, assume
    - 检索结果差/属性匹配低 → second-round ask

    Returns structured question_spec instead of pre-built NL text.
    The LLM is responsible for generating natural language from question_spec.

    Args:
        entities: Current structured entities (with missing_critical_fields from validator)
        asked_fields: Fields already asked about (to avoid repetition)

    Returns:
        {
            "should_ask": bool,
            "strategy": "ask" | "assume" | "light_ask" | "none",
            "reason": str,
            "fields": [str],            # which fields to ask about
            "question_count": int,       # how many questions in this batch
            "question_type": str | None, # semantic type of the question
            "question_spec": dict | None,# structured spec for LLM to generate NL
            "assumptions": dict | None,  # assumed values when strategy=assume
        }
    """
    missing = entities.get("missing_critical_fields", [])
    ambiguous_fields = entities.get("ambiguous_fields", [])
    category = entities.get("category")
    product_type = entities.get("product_type")
    scenario = entities.get("scenario", "")
    round_num = len(asked_fields)

    # --- Strategy decision ---

    # Already asked once and user didn't answer relevant fields → assume
    if round_num >= 1 and any(f not in asked_fields for f in missing):
        assumptions = _build_assumptions(entities, missing)
        logger.info("clarification_skip", reason="already_asked", round_num=round_num)
        return {
            "should_ask": False,
            "strategy": "assume",
            "reason": f"已追问过 {round_num} 轮，部分字段仍未提供，按假设继续",
            "fields": missing,
            "question_count": 0,
            "question_type": None,
            "question_spec": None,
            "assumptions": assumptions,
        }

    # No missing fields → no need to ask
    if not missing and not ambiguous_fields:
        return {
            "should_ask": False,
            "strategy": "none",
            "reason": "实体完整，无需追问",
            "fields": [],
            "question_count": 0,
            "question_type": None,
            "question_spec": None,
            "assumptions": None,
        }

    # Semantic ambiguity (brand/category meaning) → must ask
    semantic_ambiguity = [f for f in ambiguous_fields
                          if f not in ("gender", "product_type", "skin_type")]
    if semantic_ambiguity:
        disambig_q = entities.get("_disambiguation_question")
        logger.info("clarification_ask", reason="semantic_ambiguity", fields=semantic_ambiguity)
        spec = {
            "must_ask": semantic_ambiguity,
            "context": f"用户输入'{entities.get(semantic_ambiguity[0], '')}'存在歧义",
        }
        if disambig_q:
            spec["hint"] = disambig_q
        return {
            "should_ask": True,
            "strategy": "ask",
            "reason": f"语义歧义（{', '.join(semantic_ambiguity)}），必须追问",
            "fields": semantic_ambiguity,
            "question_count": 1,
            "question_type": "semantic_disambiguation",
            "question_spec": spec,
            "assumptions": None,
        }

    # Category-specific strategy
    if category in ("服饰", "运动"):
        has_gender = "gender" not in missing
        has_type = "product_type" not in missing

        # Both missing → prefer ask
        if not has_gender and not has_type:
            suggestions = {"product_type": ["衬衫", "西装外套"]} if category == "服饰" else ["运动上衣", "运动裤"]
            logger.info("clarification_ask", reason="clothing_both_missing")
            return {
                "should_ask": True,
                "strategy": "ask",
                "reason": "服饰推荐中 gender 和 product_type 均缺失，会显著影响召回和排序",
                "fields": ["gender", "product_type"],
                "question_count": 1,
                "question_type": "clothing_gender_and_type",
                "question_spec": {
                    "must_ask": ["gender", "product_type"],
                    "context": scenario or "日常穿搭",
                    "suggestions": {"product_type": suggestions} if isinstance(suggestions, list) else suggestions,
                },
                "assumptions": None,
            }

        # Only gender missing, product_type is specific → light ask
        if not has_gender and has_type:
            logger.info("clarification_light_ask", reason="gender_missing_type_clear")
            return {
                "should_ask": True,
                "strategy": "light_ask",
                "reason": f"product_type='{product_type}' 已明确，仅缺 gender，轻量追问",
                "fields": ["gender"],
                "question_count": 1,
                "question_type": "clothing_gender",
                "question_spec": {
                    "must_ask": ["gender"],
                    "context": f"用户要买{product_type}",
                },
                "assumptions": None,
            }

    # Skincare: skin_type missing → ask
    if category == "护肤" and "skin_type" in missing:
        logger.info("clarification_ask", reason="skincare_skin_type")
        return {
            "should_ask": True,
            "strategy": "ask",
            "reason": "护肤品类需要肤质信息",
            "fields": ["skin_type"],
            "question_count": 1,
            "question_type": "skincare_skin_type",
            "question_spec": {
                "must_ask": ["skin_type"],
                "context": "护肤推荐",
                "suggestions": {"skin_type": ["干性", "油性", "混合性", "中性", "敏感肌"]},
            },
            "assumptions": None,
        }

    # Other missing fields (price, etc.) → light ask or assume
    if "price_max" in missing:
        logger.info("clarification_light_ask", reason="price_missing")
        return {
            "should_ask": True,
            "strategy": "light_ask",
            "reason": "预算缺失，轻量追问",
            "fields": ["price_max"],
            "question_count": 1,
            "question_type": "budget",
            "question_spec": {
                "must_ask": ["price_max"],
                "context": "用户未指定预算",
            },
            "assumptions": None,
        }

    # Fallback: assume for remaining missing fields
    assumptions = _build_assumptions(entities, missing)
    logger.info("clarification_skip", reason="fallback_assume", missing=missing)
    return {
        "should_ask": False,
        "strategy": "assume",
        "reason": f"缺失字段 {missing} 影响较小，按假设继续",
        "fields": missing,
        "question_count": 0,
        "question_type": None,
        "question_spec": None,
        "assumptions": assumptions,
    }


def _build_assumptions(entities: dict, missing: list[str]) -> dict:
    """Build reasonable assumptions for missing fields."""
    assumptions = {}
    for field in missing:
        if field == "gender":
            assumptions["gender"] = "unisex"
        elif field == "product_type":
            category = entities.get("category", "")
            if category == "服饰":
                assumptions["product_type"] = "衬衫/西装外套"
            else:
                assumptions["product_type"] = None
        elif field == "skin_type":
            assumptions["skin_type"] = "中性肤质"
        elif field == "price_max":
            assumptions["price_max"] = None
    return assumptions


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
    description="智能追问决策：根据缺失字段和品类策略判断是否追问。"
                "返回结构化 question_spec（fields/question_type/question_spec），"
                "你需要根据 question_spec 生成自然流畅的追问文本。"
                "当实体有 missing_critical_fields 时，应在检索前调用此工具。",
    parameters={
        "type": "object",
        "properties": {
            "entities": {
                "type": "object",
                "description": "当前结构化实体（含 missing_critical_fields）",
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
