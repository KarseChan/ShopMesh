"""Instant-order constraint parser — rules only, no LLM.

Turns a spoken 秒送 request ("附近奶茶店30分钟送到") into structured constraints
for nearby_merchant_search. Per the plan's灵魂,约束求解走规则快路径 —— it's fully
decidable, so we never spend a ~5-10s LLM call on it. The (later) agent uses LLM
only for the dialogue/组单 layer.
"""

import re

from src.observability.logger import get_logger

logger = get_logger("instant_constraint_parser")

# 门店品类关键词 → 规范品类(与 mock_merchants 的 category 对齐)
_CATEGORY_KEYWORDS = {
    "奶茶": ["奶茶", "饮品", "喜茶", "奈雪", "茶百道", "蜜雪", "coco", "柠檬茶", "波波", "珍珠"],
    "快餐": ["快餐", "汉堡", "炸鸡", "麦当劳", "肯德基", "华莱士", "米饭", "盒饭", "午饭", "晚饭"],
    "超市便利": ["超市", "便利店", "日用", "生鲜", "美宜佳", "全家"],
    "药店": ["药店", "药", "感冒", "口罩", "创可贴"],
}

# 时效:显式分钟 / 半小时 / N 小时
_ETA_MINUTES = re.compile(r"(\d+)\s*分钟")
_ETA_HOURS = re.compile(r"(\d+)\s*个?\s*小时")
_HALF_HOUR = ("半小时", "半個小時", "30分钟内", "三十分钟")

# "附近/周边/最近/就近" → 需要用户位置
_NEARBY_KW = ("附近", "周边", "周邊", "最近", "就近", "旁边", "身边")

# 子意图关键词(P3):再来一单 / 预算内凑单
_REORDER_KW = ("再来一单", "老样子", "上次那个", "上次的", "再点一次", "还是上次", "再来一份", "跟上次一样")
_ASSEMBLE_KW = ("套餐", "凑", "组个", "组一份", "组一单", "配个", "搭配", "来一套", "配一杯", "配份")

# 预算(人均):复用 product 侧的常见表述
_BUDGET_PATTERNS = [
    re.compile(r"(?:人均|预算|大概|大约)\s*(\d+)"),
    re.compile(r"(\d+)\s*(?:元|块)?\s*(?:以内|以下|之内|封顶|左右)"),
    re.compile(r"(?:不超过|别超过|不高于|低于|最多)\s*(\d+)"),
    re.compile(r"(\d+)\s*(?:块钱|块|元)"),  # 兜底:裸"35块/40元"(组单/凑单常见)
]


def _match_category(text: str) -> str | None:
    for canonical, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return canonical
    return None


def _match_eta(text: str) -> int | None:
    if any(kw in text for kw in _HALF_HOUR):
        return 30
    m = _ETA_MINUTES.search(text)
    if m:
        return int(m.group(1))
    m = _ETA_HOURS.search(text)
    if m:
        return int(m.group(1)) * 60
    return None


def _match_budget(text: str) -> float | None:
    for pat in _BUDGET_PATTERNS:
        m = pat.search(text)
        if m:
            return float(m.group(1))
    return None


def parse_instant_constraints(text: str) -> dict:
    """Parse a 秒送 request into constraints.

    Returns:
        {
          "merchant_category": str | None,
          "max_delivery_minutes": int | None,
          "budget": float | None,
          "needs_location": bool,   # user said 附近/周边 → location required
          "semantic_query": str,    # passthrough for vector recall + taste ranking
        }
    """
    text = (text or "").strip()
    # 子意图:reorder > assemble > recall(默认就近召回)
    if any(kw in text for kw in _REORDER_KW):
        sub_intent = "reorder"
    elif any(kw in text for kw in _ASSEMBLE_KW):
        sub_intent = "assemble"
    else:
        sub_intent = "recall"
    constraints = {
        "merchant_category": _match_category(text),
        "max_delivery_minutes": _match_eta(text),
        "budget": _match_budget(text),
        "needs_location": any(kw in text for kw in _NEARBY_KW),
        "sub_intent": sub_intent,
        "semantic_query": text,
    }
    logger.info("instant_constraints_parsed",
                category=constraints["merchant_category"],
                max_eta=constraints["max_delivery_minutes"],
                budget=constraints["budget"],
                needs_location=constraints["needs_location"],
                sub_intent=sub_intent)
    return constraints
