"""LLM Router — fallback intent classification via LLM Function Calling."""

from src.models.llm_client import get_llm

INTENTS = ["search", "compare", "recommend", "detail", "order"]

SYSTEM_PROMPT = (
    "你是意图分类器。从用户输入中判断意图，返回 JSON：\n"
    '{"intent": "search|compare|recommend|detail|order", "confidence": 0.0~1.0}\n'
    "意图说明：\n"
    "- search: 搜索/查找商品\n"
    "- compare: 比价/对比不同平台价格\n"
    "- recommend: 请求推荐\n"
    "- detail: 查看商品详情/评价\n"
    "- order: 下单/购买\n"
    "只输出 JSON，不要其他文字。"
)


async def classify(query: str) -> tuple[str, float]:
    """Classify intent using LLM. Returns (intent, confidence)."""
    llm = get_llm()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    try:
        parsed = await llm.chat_json(messages)
        intent = parsed.get("intent", "")
        confidence = float(parsed.get("confidence", 0.0))
        if intent not in INTENTS:
            return "", 0.0
        return intent, confidence
    except Exception:
        return "", 0.0
