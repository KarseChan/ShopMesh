"""Fast Category Detector — rules + dictionary + BGE-M3, no LLM.

Three-layer fast path for category detection:
1. Keyword dictionary match (rules)
2. BGE-M3 embedding similarity (semantic)
3. Merge decision

Used before entity extraction to detect category switches early,
so the LLM prompt can include context hints about previous turn state.
"""

from math import sqrt

from src.config import config
from src.observability.logger import get_logger

logger = get_logger("category_detector")

# ── Category keyword dictionary ──────────────────────────────────────
# Each category maps to a list of trigger keywords.
# Longer keywords are checked first to avoid partial matches.

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "护肤": [
        "护肤品", "化妆品", "美妆", "面霜", "精华液", "精华", "面膜", "防晒霜", "防晒",
        "洗面奶", "水乳", "爽肤水", "乳液", "眼霜", "唇膏", "口红", "粉底",
        "卸妆", "护肤", "美肤", "补水保湿", "控油", "祛痘", "美白",
    ],
    "服饰": [
        "衣服", "衬衫", "T恤", "Polo衫", "卫衣", "外套", "夹克", "西装",
        "针织衫", "羽绒服", "裤子", "牛仔裤", "西裤", "休闲裤", "短裤",
        "裙子", "连衣裙", "半身裙", "长裙", "正装", "穿搭", "男装", "女装",
        "服饰", "服装", "上衣", "内搭", "马甲", "背心", "风衣", "大衣",
    ],
    "数码": [
        "手机", "电脑", "笔记本", "平板", "耳机", "充电器", "数据线", "充电宝",
        "键盘", "鼠标", "显示器", "音箱", "相机", "摄像机", "智能手表", "手环",
        "数码", "电子产品", "路由器", "硬盘", "U盘",
    ],
    "鞋靴": [
        "运动鞋", "皮鞋", "高跟鞋", "跑步鞋", "休闲鞋", "篮球鞋", "板鞋",
        "靴子", "雪地靴", "凉鞋", "拖鞋", "帆布鞋", "登山鞋",
    ],
    "箱包": [
        "双肩包", "背包", "手提包", "斜挎包", "钱包", "手拿包", "旅行箱",
        "行李箱", "拉杆箱", "电脑包", "公文包", "腰包", "单肩包",
    ],
    "食品": [
        "零食", "坚果", "巧克力", "饼干", "糖果", "果干", "肉干", "膨化食品",
        "方便面", "速食", "食品", "吃的", "小吃",
    ],
    "奶茶": [
        "奶茶", "咖啡", "饮料", "果汁", "茶饮", "奶昔", "气泡水",
        "星巴克", "瑞幸", "喜茶", "奈雪",
    ],
    "家居": [
        "家居", "家具", "床品", "被子", "枕头", "床单", "收纳", "置物架",
        "台灯", "窗帘", "地毯", "抱枕", "花瓶", "香薰", "加湿器",
    ],
    "母婴": [
        "母婴", "婴儿", "宝宝", "奶粉", "尿不湿", "纸尿裤", "奶瓶",
        "婴儿车", "儿童", "童装", "玩具", "辅食",
    ],
    "运动": [
        "运动", "健身", "跑步", "瑜伽", "游泳", "篮球", "足球", "羽毛球",
        "骑行", "登山", "户外", "运动服", "运动裤", "运动装备",
    ],
}

# Flatten all keywords into a list sorted by length descending (longest first)
# to ensure longer keywords match before shorter substrings.
_ALL_KEYWORDS: list[tuple[str, str]] = []  # (keyword, category)
for _cat, _kws in _CATEGORY_KEYWORDS.items():
    for _kw in _kws:
        _ALL_KEYWORDS.append((_kw, _cat))
_ALL_KEYWORDS.sort(key=lambda x: len(x[0]), reverse=True)

# ── Category anchor phrases for BGE-M3 semantic matching ─────────────
# Short descriptive phrases that represent each category well.

_CATEGORY_ANCHORS: dict[str, str] = {
    "护肤": "护肤品 化妆品 面霜 精华 防晒 洗面奶 补水保湿",
    "服饰": "衣服 服装 衬衫 T恤 外套 裤子 裙子 穿搭",
    "数码": "手机 电脑 笔记本 平板 耳机 数码产品 电子产品",
    "鞋靴": "运动鞋 皮鞋 高跟鞋 跑步鞋 靴子 凉鞋",
    "箱包": "双肩包 手提包 斜挎包 钱包 行李箱 旅行箱",
    "食品": "零食 坚果 巧克力 饼干 食品 小吃",
    "奶茶": "奶茶 咖啡 饮料 果汁 茶饮",
    "家居": "家居 家具 床品 收纳 台灯 装饰",
    "母婴": "母婴 婴儿 宝宝 奶粉 尿不湿 儿童用品",
    "运动": "运动 健身 跑步 瑜伽 户外 运动装备",
}

# Module-level cache for anchor embeddings
_ANCHOR_CACHE: dict[str, list[float]] | None = None


def _keyword_match(query: str) -> str | None:
    """Layer 1: Match category by keyword dictionary.

    Returns the first matching category, or None.
    Longer keywords are checked first to avoid partial matches
    (e.g., "运动鞋" should match 鞋靴, not 运动).
    """
    for keyword, category in _ALL_KEYWORDS:
        if keyword in query:
            return category
    return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sqrt(sum(x * x for x in a))
    norm_b = sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _get_anchor_embeddings() -> dict[str, list[float]]:
    """Get or compute BGE-M3 embeddings for category anchors. Cached."""
    global _ANCHOR_CACHE
    if _ANCHOR_CACHE is not None:
        return _ANCHOR_CACHE

    from src.models.embedder import get_embedder
    embedder = get_embedder()

    categories = list(_CATEGORY_ANCHORS.keys())
    phrases = list(_CATEGORY_ANCHORS.values())
    vectors = await embedder.aembed_batch(phrases)

    _ANCHOR_CACHE = dict(zip(categories, vectors))
    logger.info("category_anchors_embedded", count=len(_ANCHOR_CACHE))
    return _ANCHOR_CACHE


async def _embedding_match(query: str, threshold: float = 0.70) -> tuple[str | None, float]:
    """Layer 2: Match category via BGE-M3 embedding similarity.

    Returns (category, similarity) if above threshold, else (None, best_sim).
    """
    from src.models.embedder import get_embedder
    embedder = get_embedder()

    anchors = await _get_anchor_embeddings()
    query_vec = await embedder.aembed(query)

    best_cat = None
    best_sim = 0.0
    for cat, vec in anchors.items():
        sim = _cosine_similarity(query_vec, vec)
        if sim > best_sim:
            best_sim = sim
            best_cat = cat

    if best_sim >= threshold:
        return best_cat, best_sim
    return None, best_sim


async def detect_category(query: str) -> tuple[str | None, float]:
    """Fast category detection without LLM.

    Layer 1: keyword dictionary match (rules)
    Layer 2: BGE-M3 embedding similarity (semantic)

    Returns (category, confidence). None if no category detected.
    """
    # Layer 1: Rules
    cat = _keyword_match(query)
    if cat:
        logger.info("category_detected", category=cat, method="keyword", confidence=0.95)
        return cat, 0.95

    # Layer 2: BGE-M3
    cat, sim = await _embedding_match(query)
    if cat:
        logger.info("category_detected", category=cat, method="embedding",
                     confidence=round(sim, 3))
        return cat, sim

    logger.info("category_not_detected", best_sim=round(sim, 3))
    return None, 0.0
