"""Entity Extractor — extract structured fields from user input via LLM.

Output schema:
{
    "category": "品类 or null",
    "price_min": 数字 or null,
    "price_max": 数字 or null,
    "brand": "品牌 or null",
    "scenario": "场景 or null",
    "quantity": 数字 or null,
    "ambiguous": true/false,
    "ambiguous_fields": ["不确定的字段"]
}
"""

from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("entity_extractor")

SYSTEM_PROMPT = (
    "你是实体提取器。从用户输入中抽取以下字段，返回 JSON：\n"
    "{\n"
    '  "category": "品类或null",\n'
    '  "price_min": 数字或null,\n'
    '  "price_max": 数字或null,\n'
    '  "brand": "品牌或null",\n'
    '  "scenario": "场景或null",\n'
    '  "quantity": 数字或null,\n'
    '  "ambiguous": true/false,\n'
    '  "ambiguous_fields": ["不确定的字段"]\n'
    "}\n"
    "品类只限：护肤、奶茶、数码、服饰、食品、家居、母婴、运动\n"
    "歧义标记：当实体含义不确定时设为 true（如'苹果'可能是水果或手机），"
    "并把不确定的字段名加入 ambiguous_fields。\n"
    "只输出 JSON，不要其他文字。"
)


async def extract_entities(query: str) -> dict:
    """Extract structured entities from user query.

    Returns dict with category, price_min, price_max, brand, scenario,
    quantity, ambiguous, ambiguous_fields.
    """
    llm = get_llm()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    try:
        result = await llm.chat_json(messages)
        # Normalize: ensure all keys exist
        entities = {
            "category": result.get("category"),
            "price_min": result.get("price_min"),
            "price_max": result.get("price_max"),
            "brand": result.get("brand"),
            "scenario": result.get("scenario"),
            "quantity": result.get("quantity"),
            "ambiguous": result.get("ambiguous", False),
            "ambiguous_fields": result.get("ambiguous_fields", []),
        }
        logger.info("entities_extracted", entities=entities)
        return entities
    except Exception as e:
        logger.error("entity_extraction_failed", error_type=type(e).__name__, error_message=str(e))
        # Fallback: return raw query as keyword, mark ambiguous
        return {
            "category": None,
            "price_min": None,
            "price_max": None,
            "brand": None,
            "scenario": None,
            "quantity": None,
            "ambiguous": True,
            "ambiguous_fields": ["query"],
            "_raw_query": query,
        }
