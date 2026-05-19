"""Entity Extractor — extract structured fields from user input via LLM.

Output schema:
{
    "category": "大品类 or null",
    "product_type": "具体商品词 or null",
    "price_min": 数字 or null,
    "price_max": 数字 or null,
    "brand": "品牌 or null",
    "scenario": "场景 or null",
    "quantity": 数字 or null,
    "preference": "偏好 or null",
    "skin_type": "肤质 or null (护肤)",
    "concerns": "护肤需求 or null (护肤)",
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
    '  "category": "大品类或null",\n'
    '  "product_type": "具体商品词或null",\n'
    '  "price_min": 数字或null,\n'
    '  "price_max": 数字或null,\n'
    '  "brand": "品牌或null",\n'
    '  "scenario": "场景或null",\n'
    '  "quantity": 数字或null,\n'
    '  "preference": "偏好关键词或null",\n'
    '  "skin_type": "肤质或null（如油皮、干皮、敏感肌，仅护肤品类）",\n'
    '  "concerns": "护肤需求或null（如补水保湿、控油祛痘、抗老紧致，仅护肤品类）",\n'
    '  "ambiguous": true/false,\n'
    '  "ambiguous_fields": ["不确定的字段"]\n'
    "}\n"
    "品类只限：护肤、奶茶、数码、服饰、食品、家居、母婴、运动\n"
    "product_type：用户提到的具体商品词，如'衬衫'、'T恤'、'外套'、'裤装'、'面膜'等。提取原词，不要改写。\n"
    "preference：用户对商品品质的偏好描述，如'口碑好'、'销量高'、'大牌'、'便宜'、'轻薄'等。提取原词，不要改写。\n"
    "skin_type/concerns：仅当品类是护肤时才提取，其他品类设为null。\n"
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
            "product_type": result.get("product_type"),
            "price_min": result.get("price_min"),
            "price_max": result.get("price_max"),
            "brand": result.get("brand"),
            "scenario": result.get("scenario"),
            "quantity": result.get("quantity"),
            "preference": result.get("preference"),
            "skin_type": result.get("skin_type"),
            "concerns": result.get("concerns"),
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
            "product_type": None,
            "price_min": None,
            "price_max": None,
            "brand": None,
            "scenario": None,
            "quantity": None,
            "preference": None,
            "skin_type": None,
            "concerns": None,
            "ambiguous": True,
            "ambiguous_fields": ["query"],
            "_raw_query": query,
        }
