"""Search Planner — generate search strategy before retrieval.

Responsibilities:
1. Determine if multi-query search is needed (rule-based)
2. If needed, use LLM to generate search_requests (query + product_type)
3. Return structured SearchPlan for downstream tools

Trigger rules for multi-query:
- product_type in ["衣服", "穿搭", "搭配", "一套", "套装"]
- scenario in ["面试", "上班", "通勤", "约会", "婚礼", "答辩"] and product_type is vague/empty
- soft_requirements >= 2 and product_type is vague

Single mode (no planning needed):
- product_type is specific (双肩包, 衬衫, 西裤, etc.)
- Pure compare/detail/review intent
"""

from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("search_planner")

# Vague product types that need expansion
_VAGUE_PRODUCT_TYPES = {"衣服", "穿搭", "搭配", "一套", "套装", "服装", "服饰"}

# Scenarios that benefit from multi-query outfit search
_OUTFIT_SCENARIOS = {"面试", "上班", "通勤", "约会", "婚礼", "答辩", "出差", "旅行", "毕业典礼"}

# Well-defined product types that don't need planning
_SPECIFIC_PRODUCT_TYPES = {
    "衬衫", "T恤", "Polo衫", "卫衣", "外套", "夹克", "西装", "针织衫", "羽绒服",
    "裤子", "裤装", "牛仔裤", "西裤", "休闲裤", "短裤", "裙子", "裙装", "半身裙", "长裙",
    "双肩包", "背包", "手提包", "斜挎包", "钱包",
    "运动鞋", "皮鞋", "跑步鞋", "休闲鞋", "高跟鞋",
    "面膜", "精华", "面霜", "防晒", "洗面奶",
}

_PLANNER_PROMPT = (
    "你是电商搜索规划器。根据用户需求，生成多条检索请求。\n\n"
    "规则：\n"
    "1. 选择 3-5 个最相关的具体商品类型（product_type）\n"
    "2. 为每个商品类型生成一个语义查询（query），包含场景和风格描述\n"
    "3. query 要简洁，突出场景+风格+品类，不超过 15 字\n"
    "4. 最后一条保留原始用户查询，product_type 设为 null\n\n"
    "可用的商品类型（从以下选择，不要自创）：\n"
    "衬衫, T恤, Polo衫, 卫衣, 外套, 夹克, 西装, 针织衫, 羽绒服, "
    "裤子, 裤装, 牛仔裤, 西裤, 休闲裤, 短裤, 裙子, 裙装, 半身裙, 长裙, "
    "双肩包, 背包, 手提包, 斜挎包, 钱包, "
    "运动鞋, 皮鞋, 跑步鞋, 休闲鞋, 高跟鞋\n\n"
    "输出 JSON：\n"
    "{\n"
    '  "target_product_types": ["衬衫", "西装外套", "西裤"],\n'
    '  "search_requests": [\n'
    '    {"query": "面试简约利落衬衫", "product_type": "衬衫"},\n'
    '    {"query": "面试轻商务西装外套", "product_type": "西装外套"},\n'
    '    {"query": "用户原始查询", "product_type": null}\n'
    "  ]\n"
    "}\n"
    "只输出 JSON，不要其他文字。"
)


def _is_vague_product_type(product_type: str | None) -> bool:
    """Check if product_type is vague and needs expansion."""
    if not product_type:
        return True
    return product_type in _VAGUE_PRODUCT_TYPES


def _should_use_multi_query(entities: dict) -> tuple[bool, str]:
    """Rule-based check: should we use multi-query search?

    Returns (should_plan, reason).
    """
    product_type = entities.get("product_type")
    scenario = entities.get("scenario")
    soft_reqs = entities.get("soft_requirements", [])

    # Rule 1: vague product type
    if product_type and product_type in _VAGUE_PRODUCT_TYPES:
        return True, f"product_type '{product_type}' 是模糊品类，需要扩展为具体商品类型"

    # Rule 2: outfit scenario + vague/empty product type
    if scenario and scenario in _OUTFIT_SCENARIOS and _is_vague_product_type(product_type):
        return True, f"场景 '{scenario}' + 模糊品类，需要多品类穿搭检索"

    # Rule 3: multiple soft requirements + vague product type
    if len(soft_reqs) >= 2 and _is_vague_product_type(product_type):
        return True, f"多个软需求 ({len(soft_reqs)}) + 模糊品类，需要扩展检索"

    return False, ""


def _build_llm_context(entities: dict, user_query: str) -> str:
    """Build context string for LLM planner."""
    parts = [f"用户查询：{user_query}"]

    category = entities.get("category")
    if category:
        parts.append(f"品类：{category}")

    product_type = entities.get("product_type")
    if product_type:
        parts.append(f"商品类型：{product_type}")

    scenario = entities.get("scenario")
    if scenario:
        parts.append(f"场景：{scenario}")

    gender = entities.get("gender")
    if gender:
        parts.append(f"性别：{gender}")

    preference = entities.get("preference")
    if preference:
        parts.append(f"偏好：{preference}")

    price_max = entities.get("price_max")
    if price_max:
        parts.append(f"最高价格：{price_max}元")

    soft_reqs = entities.get("soft_requirements", [])
    if soft_reqs:
        req_texts = [r.get("canonical") or r.get("raw_text") or r.get("text", "") for r in soft_reqs]
        parts.append(f"软需求：{', '.join(req_texts)}")

    return "\n".join(parts)


async def _generate_search_requests(entities: dict, user_query: str) -> dict:
    """Use LLM to generate search requests for multi-query mode."""
    llm = get_llm()
    context = _build_llm_context(entities, user_query)

    messages = [
        {"role": "system", "content": _PLANNER_PROMPT},
        {"role": "user", "content": context},
    ]

    try:
        result = await llm.chat_json(messages)
        search_requests = result.get("search_requests", [])
        target_types = result.get("target_product_types", [])

        # Validate and clean
        valid_requests = []
        for req in search_requests:
            query = req.get("query", "").strip()
            if not query:
                continue
            pt = req.get("product_type")
            # Validate product_type is in known list
            if pt and pt not in _SPECIFIC_PRODUCT_TYPES:
                pt = None  # Reset if unknown
            valid_requests.append({
                "query": query,
                "product_type": pt,
                "top_k": 10,
            })

        # Ensure at least one request with original query
        if not any(r.get("product_type") is None for r in valid_requests):
            valid_requests.append({
                "query": user_query,
                "product_type": None,
                "top_k": 10,
            })

        return {
            "target_product_types": target_types,
            "search_requests": valid_requests,
        }

    except Exception as e:
        logger.error("planner_llm_failed", error=str(e))
        # Fallback: single request with original query
        return {
            "target_product_types": [],
            "search_requests": [{
                "query": user_query,
                "product_type": None,
                "top_k": 10,
            }],
        }


async def plan_search(entities: dict, user_query: str) -> dict:
    """Generate search plan based on entities and user query.

    Returns a SearchPlan dict:
    - search_mode: "single" or "outfit_multi_query"
    - reason: why this mode was chosen
    - primary_constraints: shared filter conditions
    - target_product_types: list of product types to search
    - search_requests: list of {query, product_type, top_k}
    """
    should_plan, reason = _should_use_multi_query(entities)

    if not should_plan:
        logger.info("search_plan", mode="single", reason="product_type is specific or not needed")
        return {
            "search_mode": "single",
            "reason": "商品类型明确，单次检索即可",
            "primary_constraints": _extract_constraints(entities),
            "target_product_types": [entities["product_type"]] if entities.get("product_type") else [],
            "search_requests": [],
        }

    # Multi-query mode: use LLM to generate search requests
    logger.info("search_plan_trigger", reason=reason)
    llm_result = await _generate_search_requests(entities, user_query)

    plan = {
        "search_mode": "outfit_multi_query",
        "reason": reason,
        "primary_constraints": _extract_constraints(entities),
        "target_product_types": llm_result["target_product_types"],
        "search_requests": llm_result["search_requests"],
    }

    logger.info("search_plan_generated",
                mode="outfit_multi_query",
                target_types=plan["target_product_types"],
                request_count=len(plan["search_requests"]))

    return plan


def _extract_constraints(entities: dict) -> dict:
    """Extract shared constraints from entities."""
    constraints = {}
    for key in ["category", "gender", "scenario", "price_min", "price_max", "brand"]:
        val = entities.get(key)
        if val is not None:
            constraints[key] = val
    return constraints
