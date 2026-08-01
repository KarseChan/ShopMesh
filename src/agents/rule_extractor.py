"""Rule-based entity extraction fast-path (P-4).

网关只有一个慢推理模型(单次 ~15s)。对**简单、常见**的导购 query,用 regex +
关键词确定性地抽实体,直接跳过那次慢 LLM;抽不确定时返回 None,调用方回退到 LLM。

保守原则(宁可回退,不降准):
- 必须有 searchable anchor(命中 product_type,或高置信 category),否则 None
- query 去掉已识别 token + 停用词后仍有较多描述性内容(软需求)→ None,交给 LLM
- 礼物/场景类(通常无明确品类)自然落到"无 anchor"→ None,交给 LLM
"""

import re

from src.observability.logger import get_logger

logger = get_logger("rule_extractor")

# ── 价格 ──
_PRICE_RANGE = re.compile(r"(\d+)\s*(?:元|块)?\s*[-~到至]\s*(\d+)")
_PRICE_MAX = [
    re.compile(r"(\d+)\s*(?:元|块|rmb)?\s*(?:以内|以下|之内|封顶|上下|左右)"),
    re.compile(r"(?:预算|大约|大概)\s*(\d+)"),
    re.compile(r"(?:不超过|别超过|不高于|低于|少于|最多)\s*(\d+)"),
]
_PRICE_MIN = [re.compile(r"(\d+)\s*(?:元|块)?\s*(?:以上|起步?|不低于|至少|最少)")]

_GENDER_M = re.compile(r"男(?:士|生|款|式|孩|童)?|先生")
_GENDER_F = re.compile(r"女(?:士|生|款|式|孩|童)?|小姐|少女")

_GIFT_KW = ("礼物", "送人", "送女", "送男", "送朋友", "礼品", "生日", "情人节", "七夕", "纪念日", "表白")

# 去 anchor/token 后判断 query 是否"简单"用的停用词
_STOPWORDS = (
    "推荐", "几双", "几个", "几件", "一双", "一个", "一件", "帮我", "我想", "想买", "想要",
    "买", "要", "找", "找找", "有没有", "有木有", "来点", "来个", "看看", "看下", "给我",
    "的", "吧", "呢", "啊", "了", "元", "块", "钱", "价格", "预算", "左右", "以内", "以下",
    "性价比", "高", "款", "适合", "男", "女",
)


def _extract_price(text: str):
    m = _PRICE_RANGE.search(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return min(a, b), max(a, b)
    pmax = pmin = None
    for pat in _PRICE_MAX:
        m = pat.search(text)
        if m:
            pmax = int(m.group(1))
            break
    for pat in _PRICE_MIN:
        m = pat.search(text)
        if m:
            pmin = int(m.group(1))
            break
    return pmin, pmax


def _extract_gender(text: str):
    if _GENDER_M.search(text):
        return "男"
    if _GENDER_F.search(text):
        return "女"
    return None


_PT_CACHE = None
_BRAND_CACHE = None
_PT2CAT_CACHE = None


def _known():
    """(product_types 按长度降序, brands 按长度降序, product_type→category)。惰性缓存。"""
    global _PT_CACHE, _BRAND_CACHE, _PT2CAT_CACHE
    if _PT_CACHE is None:
        from src.retrieval.filter_builder import _get_all_product_types, _get_product_type_to_categories
        from src.tools.search_tool import load_products
        _PT_CACHE = sorted(_get_all_product_types(), key=len, reverse=True)
        _BRAND_CACHE = sorted(
            {p["brand"] for p in load_products() if p.get("brand")}, key=len, reverse=True
        )
        _PT2CAT_CACHE = _get_product_type_to_categories()
    return _PT_CACHE, _BRAND_CACHE, _PT2CAT_CACHE


def rule_extract(user_input: str, detected_category: str | None,
                 detected_confidence: float) -> dict | None:
    """尝试纯规则抽实体。成功返回 entities dict,不确定返回 None(回退 LLM)。"""
    text = user_input.strip()

    # 礼物/场景类通常没有明确品类,交给 LLM(它要处理 scenario/soft_requirements)
    if any(kw in text for kw in _GIFT_KW):
        return None

    product_types, brands, pt2cat = _known()

    product_type = next((pt for pt in product_types if pt in text), None)
    brand = next((b for b in brands if b in text), None)
    pmin, pmax = _extract_price(text)
    gender = _extract_gender(text)

    # 品类:优先从 product_type 反查真实数据品类(pt2cat 派生自实际 5k 数据,
    # 比 category_detector 的旧 taxonomy 可靠),否则用高置信检测结果兜底
    category = None
    if product_type and product_type in pt2cat:
        cats = pt2cat[product_type]
        if len(cats) == 1:
            category = cats[0]
    if category is None and detected_category and detected_confidence >= 0.8:
        category = detected_category

    # 必须有 searchable anchor
    if not product_type and not category:
        return None

    # 简单性检查:去掉已识别 token + 停用词后,剩余中文字符不能太多(否则可能有软需求)
    residual = text
    for token in filter(None, [product_type, brand, category, str(pmax) if pmax else "",
                               str(pmin) if pmin else ""]):
        residual = residual.replace(token, "")
    for sw in _STOPWORDS:
        residual = residual.replace(sw, "")
    residual = re.sub(r"[0-9\s，。、~\-到至的和与]", "", residual)
    if len(residual) > 3:  # 仍有较多未识别描述性内容 → 交给 LLM
        logger.info("rule_fastpath_skip", reason="residual_too_long", residual=residual[:20])
        return None

    hard = {k: v for k, v in (
        ("product_type", product_type), ("category", category),
        ("price_max", pmax), ("price_min", pmin), ("brand", brand),
    ) if v is not None}

    entities = {
        "category": category,
        "product_type": product_type,
        "gender": gender,
        "price_min": pmin,
        "price_max": pmax,
        "brand": brand,
        "scenario": None,
        "quantity": None,
        "preference": None,
        "skin_type": None,
        "concerns": None,
        "hard_constraints": hard,
        "soft_requirements": [],
        "ambiguous": False,
        "ambiguous_fields": [],
    }
    logger.info("rule_fastpath_hit", category=category, product_type=product_type,
                gender=gender, price_max=pmax, brand=brand)
    return entities
