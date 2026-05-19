"""Entity Extractor — extract structured fields from user input via LLM.

Output schema:
{
    "category": "大品类 or null",
    "product_type": "具体商品词 or null",
    "gender": "男/女 or null",
    "price_min": 数字 or null,
    "price_max": 数字 or null,
    "brand": "品牌 or null",
    "scenario": "场景 or null",
    "quantity": 数字 or null,
    "preference": "偏好 or null",
    "skin_type": "肤质 or null (护肤)",
    "concerns": "护肤需求 or null (护肤)",
    "hard_constraints": {"product_type": "衬衫", "gender": "男"},
    "soft_requirements": [{"text": "夏天穿", "type": "season_scene", "importance": 0.8}],
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
    '  "gender": "男/女或null",\n'
    '  "price_min": 数字或null,\n'
    '  "price_max": 数字或null,\n'
    '  "brand": "品牌或null",\n'
    '  "scenario": "场景或null",\n'
    '  "quantity": 数字或null,\n'
    '  "preference": "偏好关键词或null",\n'
    '  "skin_type": "肤质或null（如油皮、干皮、敏感肌，仅护肤品类）",\n'
    '  "concerns": "护肤需求或null（如补水保湿、控油祛痘、抗老紧致，仅护肤品类）",\n'
    '  "hard_constraints": {},\n'
    '  "soft_requirements": [],\n'
    '  "ambiguous": true/false,\n'
    '  "ambiguous_fields": ["不确定的字段"]\n'
    "}\n"
    "品类只限：护肤、奶茶、数码、服饰、食品、家居、母婴、运动\n"
    "product_type：用户提到的具体商品词，如'衬衫'、'T恤'、'外套'、'裤装'、'面膜'等。提取原词，不要改写。\n"
    "gender：用户明确提到的性别，如'男士'→'男'、'女生'→'女'、'男款'→'男'、'女款'→'女'。未提及则为null。\n"
    "preference：用户对商品品质的偏好描述，如'口碑好'、'销量高'、'大牌'、'便宜'、'轻薄'等。提取原词，不要改写。\n"
    "skin_type/concerns：仅当品类是护肤时才提取，其他品类设为null。\n"
    "hard_constraints：硬过滤条件，从已提取字段中选取必须满足的条件。"
    "通常包含 product_type（如有）和 gender（如有），也可能包含 brand、price_max 等。\n"
    "soft_requirements：软需求列表，用户提到的非硬性偏好。每个元素：\n"
    '  {"raw_text": "用户原话", "canonical": "核心需求词", "type": "类型", "importance": 0.5-1.0}\n'
    "  raw_text：用户原话摘录，如'适合通勤'、'夏天穿的'、'想要轻薄的'\n"
    "  canonical：去掉修饰词后的核心需求词，如'通勤'、'夏天'、'轻薄'\n"
    "  去掉的修饰词：适合、适用于、用于、想要、推荐、几款、一些、比较、非常、特别、需要、找、看看、有没有\n"
    "  type 示例：season_scene（季节/场景）、functional_preference（功能偏好）、"
    "style_preference（风格偏好）、quality_signal（品质信号）、gift_context（送礼场景）\n"
    "  importance：用户语气强弱，'必须/一定要'→1.0，'最好/希望'→0.7，'如果能/顺便'→0.5\n"
    "  从 scenario、preference、原始描述中提取，拆分为独立的软需求条目。\n"
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
            "gender": result.get("gender"),
            "price_min": result.get("price_min"),
            "price_max": result.get("price_max"),
            "brand": result.get("brand"),
            "scenario": result.get("scenario"),
            "quantity": result.get("quantity"),
            "preference": result.get("preference"),
            "skin_type": result.get("skin_type"),
            "concerns": result.get("concerns"),
            "hard_constraints": result.get("hard_constraints", {}),
            "soft_requirements": result.get("soft_requirements", []),
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
            "gender": None,
            "price_min": None,
            "price_max": None,
            "brand": None,
            "scenario": None,
            "quantity": None,
            "preference": None,
            "skin_type": None,
            "concerns": None,
            "hard_constraints": {},
            "soft_requirements": [],
            "ambiguous": True,
            "ambiguous_fields": ["query"],
            "_raw_query": query,
        }
