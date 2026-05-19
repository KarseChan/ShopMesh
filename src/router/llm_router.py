"""LLM Router — fallback intent classification via LLM Function Calling.

Three-layer intent structure:
- user_goal: What the user wants (recommend/find/compare/detail/order)
- task_type: Task complexity (shopping_advice vs product_search)
- execution_hint: How to execute (contextual_search vs direct_search)
"""

from src.models.llm_client import get_llm

USER_GOALS = ["recommend_product", "find_product", "compare_products", "view_detail", "place_order"]
TASK_TYPES = ["shopping_advice", "product_search", "outfit_planning", "price_comparison", "detail_inquiry", "order_placement"]
EXECUTION_HINTS = ["contextual_search", "direct_search", "multi_query", "compare", "get_detail", "clarify_first"]

SYSTEM_PROMPT = (
    "你是意图分类器。从用户输入中判断意图，返回 JSON：\n"
    "{\n"
    '  "user_goal": "recommend_product|find_product|compare_products|view_detail|place_order",\n'
    '  "task_type": "shopping_advice|product_search|outfit_planning|price_comparison|detail_inquiry|order_placement",\n'
    '  "execution_hint": "contextual_search|direct_search|multi_query|compare|get_detail|clarify_first",\n'
    '  "confidence": 0.0~1.0\n'
    "}\n"
    "判断规则：\n\n"
    "user_goal（用户目标）：\n"
    "- recommend_product: 用户请求推荐/建议（推荐几款/帮我选/有什么好的）\n"
    "- find_product: 用户明确要找某商品（找衬衫/买手机/搜一下）\n"
    "- compare_products: 比价/对比（A和B哪个好/哪个平台便宜）\n"
    "- view_detail: 查看详情（这款怎么样/评价如何）\n"
    "- place_order: 下单购买（我要买这个/下单）\n\n"
    "task_type（任务复杂度）：\n"
    "- shopping_advice: 有场景/风格/偏好描述（面试/通勤/正式/不老气/适合夏天）\n"
    "- product_search: 简单找商品，无复杂场景（找衬衫/买手机）\n"
    "- outfit_planning: 用户要搭一套/穿搭/组合（搭一套通勤穿搭/面试套装）\n"
    "- price_comparison: 比价任务\n"
    "- detail_inquiry: 详情查询\n"
    "- order_placement: 下单任务\n\n"
    "execution_hint（执行建议）：\n"
    "- clarify_first: 品类模糊+场景（衣服+面试 → 需要先澄清性别/品类）\n"
    "- contextual_search: 有场景但品类明确（衬衫+面试 → 直接搜索，带推荐理由）\n"
    "- direct_search: 简单搜索，无场景（找衬衫 → 直接搜索）\n"
    "- multi_query: 需要多品类组合（搭一套 → 多query检索）\n"
    "- compare: 比价执行\n"
    "- get_detail: 详情查询执行\n\n"
    "示例：\n"
    "- '帮我找男款衬衫' → {user_goal:find_product, task_type:product_search, execution_hint:direct_search}\n"
    "- '推荐几款300以内的双肩包' → {user_goal:recommend_product, task_type:product_search, execution_hint:direct_search}\n"
    "- '我下周要面试，想买一件正式但不老气的衣服' → {user_goal:recommend_product, task_type:shopping_advice, execution_hint:clarify_first}\n"
    "- '帮我搭一套通勤穿搭' → {user_goal:recommend_product, task_type:outfit_planning, execution_hint:multi_query}\n"
    "- '有没有适合夏天穿的衬衫' → {user_goal:recommend_product, task_type:shopping_advice, execution_hint:contextual_search}\n"
    "- 'A和B哪个便宜' → {user_goal:compare_products, task_type:price_comparison, execution_hint:compare}\n"
    "只输出 JSON，不要其他文字。"
)


async def classify(query: str) -> tuple[dict, float]:
    """Classify intent using LLM. Returns (intent_dict, confidence)."""
    llm = get_llm()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    try:
        parsed = await llm.chat_json(messages)
        user_goal = parsed.get("user_goal", "")
        task_type = parsed.get("task_type", "")
        execution_hint = parsed.get("execution_hint", "")
        confidence = float(parsed.get("confidence", 0.0))

        # Validate
        if user_goal not in USER_GOALS:
            return {}, 0.0
        if task_type not in TASK_TYPES:
            task_type = "product_search"  # fallback
        if execution_hint not in EXECUTION_HINTS:
            execution_hint = "direct_search"  # fallback

        return {
            "user_goal": user_goal,
            "task_type": task_type,
            "execution_hint": execution_hint,
        }, confidence
    except Exception:
        return {}, 0.0
