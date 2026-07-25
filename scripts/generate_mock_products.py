"""Batch mock product generator for evaluation and demo.

Usage:
    python scripts/generate_mock_products.py [--count 5000] [--seed 42] [--output data/mock_products_5k.json]

Pure Python rule-based generation, zero LLM calls.

Data model (v2 — semantically coherent):
    Category → Subtype (产品线) → Brand → Variant → per-platform SKU

Price / features / naming / image are owned by the **subtype**, not the
category. Brands are bound to the subtypes they actually make. This fixes the
v1 defect where e.g. 清风(卫生纸) shared 家居's (5, 4999) range and got priced
at ¥2252. Now 抽纸 has its own (15, 129) range and its own brand set.
"""

import argparse
import json
import random
import sys
from pathlib import Path

# Windows consoles default to GBK and choke on ¥ / CJK in stdout; the JSON file
# itself is always written UTF-8. Reconfigure stdout so the stats report prints.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ── Catalog: category → list of subtypes ──
# Each subtype: noun(产品名词), price(min,max), features, brands, specs(规格/系列)
CATALOG = {
    "护肤": [
        {"noun": "精华液", "price": (150, 2800), "brands": ["雅诗兰黛", "兰蔻", "SK-II", "资生堂", "科颜氏", "赫莲娜", "海蓝之谜", "CPB", "雪花秀", "后", "珀莱雅", "薇诺娜"],
         "features": ["抗老", "紧致", "淡斑", "修护", "提亮"], "specs": ["30ml", "50ml", "75ml"]},
        {"noun": "面霜", "price": (120, 3500), "brands": ["雅诗兰黛", "兰蔻", "资生堂", "海蓝之谜", "CPB", "科颜氏", "玉兰油", "自然堂", "珀莱雅"],
         "features": ["保湿", "抗老", "紧致", "滋润", "修护"], "specs": ["50g", "30g"]},
        {"noun": "洁面乳", "price": (39, 399), "brands": ["珂润", "芙丽芳丝", "理肤泉", "雅漾", "欧莱雅", "百雀羚", "自然堂", "资生堂"],
         "features": ["氨基酸", "温和", "清洁", "控油", "舒缓"], "specs": ["100g", "120g", "150g"]},
        {"noun": "面膜", "price": (29, 599), "brands": ["珀莱雅", "自然堂", "百雀羚", "薇诺娜", "欧莱雅", "IPSA"],
         "features": ["补水", "美白", "修护", "舒缓", "紧致"], "specs": ["5片装", "10片装"]},
        {"noun": "防晒霜", "price": (59, 499), "brands": ["资生堂", "理肤泉", "雅漾", "珂润", "欧莱雅"],
         "features": ["SPF50", "防水", "轻薄", "养肤", "隔离"], "specs": ["30ml", "50ml"]},
        {"noun": "爽肤水", "price": (49, 899), "brands": ["兰蔻", "倩碧", "IPSA", "雪花秀", "黛珂", "娇韵诗"],
         "features": ["保湿", "二次清洁", "舒缓", "提亮", "平衡"], "specs": ["150ml", "200ml"]},
    ],
    "奶茶": [
        {"noun": "水果茶", "price": (9, 32), "brands": ["古茗", "喜茶", "奈雪", "蜜雪冰城", "茶百道", "一点点", "CoCo", "沪上阿姨", "霸王茶姬", "柠季"],
         "features": ["杨枝甘露", "柠檬", "芒果", "草莓", "少糖"], "specs": ["中杯", "大杯", "超大杯"]},
        {"noun": "奶盖茶", "price": (12, 35), "brands": ["喜茶", "奈雪", "茶颜悦色", "古茗", "霸王茶姬", "贡茶"],
         "features": ["芝士奶盖", "茉莉", "乌龙", "少冰", "无糖"], "specs": ["中杯", "大杯"]},
        {"noun": "珍珠奶茶", "price": (8, 28), "brands": ["蜜雪冰城", "一点点", "CoCo", "益禾堂", "甜啦啦", "快乐柠檬", "85度C"],
         "features": ["珍珠", "椰果", "布丁", "波霸", "红豆"], "specs": ["中杯", "大杯", "超大杯"]},
        {"noun": "烧仙草", "price": (10, 26), "brands": ["书亦烧仙草", "悸动烧仙草", "沪上阿姨", "蜜雪冰城"],
         "features": ["芋圆", "花生", "红豆", "椰果", "招牌"], "specs": ["中杯", "大杯"]},
        {"noun": "现磨咖啡", "price": (9, 38), "brands": ["瑞幸", "星巴克", "CoCo", "沪上阿姨"],
         "features": ["生椰拿铁", "美式", "燕麦", "厚乳", "冰"], "specs": ["中杯", "大杯"]},
    ],
    "数码": [
        {"noun": "智能手机", "price": (999, 9999), "brands": ["Apple", "华为", "小米", "三星", "OPPO", "vivo", "荣耀", "一加", "realme", "iQOO"],
         "features": ["5G", "快充", "高刷", "长续航", "高清影像"], "specs": ["128GB", "256GB", "512GB"]},
        {"noun": "笔记本电脑", "price": (2999, 15999), "brands": ["联想", "戴尔", "惠普", "华硕", "宏碁", "Apple", "华为", "小米"],
         "features": ["轻薄", "高性能", "长续航", "高刷屏", "独显"], "specs": ["16G+512G", "32G+1T", "16G+1T"]},
        {"noun": "无线耳机", "price": (99, 2999), "brands": ["Apple", "索尼", "Bose", "JBL", "Beats", "铁三角", "华为", "小米", "三星"],
         "features": ["主动降噪", "长续航", "入耳式", "高清音质", "低延迟"], "specs": ["标准版", "Pro"]},
        {"noun": "平板电脑", "price": (1299, 8999), "brands": ["Apple", "华为", "小米", "三星", "联想"],
         "features": ["高刷屏", "手写笔", "轻薄", "长续航", "大内存"], "specs": ["128GB", "256GB"]},
        {"noun": "智能手表", "price": (199, 3999), "brands": ["Apple", "华为", "小米", "三星", "佳明"],
         "features": ["心率监测", "血氧", "GPS", "长续航", "运动模式"], "specs": ["标准版", "旗舰版"]},
        {"noun": "无人机", "price": (1999, 8999), "brands": ["大疆", "GoPro"],
         "features": ["4K航拍", "避障", "长续航", "轻便", "增稳"], "specs": ["标准版", "畅飞套装"]},
    ],
    "服饰": [
        {"noun": "T恤", "price": (39, 399), "brands": ["Nike", "Adidas", "优衣库", "ZARA", "H&M", "李宁", "安踏", "太平鸟", "森马", "海澜之家"],
         "features": ["纯棉", "透气", "百搭", "宽松", "印花"], "specs": ["S", "M", "L", "XL"]},
        {"noun": "卫衣", "price": (99, 699), "brands": ["Nike", "Adidas", "优衣库", "李宁", "GXG", "太平鸟", "UR"],
         "features": ["加绒", "连帽", "宽松", "潮流", "保暖"], "specs": ["M", "L", "XL"]},
        {"noun": "连衣裙", "price": (99, 899), "brands": ["ZARA", "H&M", "UR", "ONLY", "VERO MODA", "太平鸟", "森马"],
         "features": ["修身", "显瘦", "时尚", "碎花", "通勤"], "specs": ["S", "M", "L"]},
        {"noun": "羽绒服", "price": (299, 2999), "brands": ["波司登", "鄂尔多斯", "海澜之家", "Nike", "Adidas", "优衣库"],
         "features": ["90绒", "保暖", "轻薄", "防风", "连帽"], "specs": ["M", "L", "XL"]},
        {"noun": "运动鞋", "price": (199, 1299), "brands": ["Nike", "Adidas", "李宁", "安踏", "特步", "361°", "匹克"],
         "features": ["缓震", "透气", "轻便", "防滑", "百搭"], "specs": ["40", "41", "42", "43"]},
        {"noun": "牛仔裤", "price": (99, 599), "brands": ["优衣库", "ZARA", "H&M", "JACK & JONES", "GXG", "森马"],
         "features": ["修身", "弹力", "直筒", "百搭", "水洗"], "specs": ["29", "30", "31", "32"]},
    ],
    "食品": [
        {"noun": "坚果零食", "price": (19, 159), "brands": ["三只松鼠", "良品铺子", "百草味", "来伊份", "洽洽"],
         "features": ["每日坚果", "混合装", "原味", "分享装", "无添加"], "specs": ["500g", "750g", "1kg"]},
        {"noun": "饼干糕点", "price": (5, 89), "brands": ["奥利奥", "达利园", "徐福记", "旺旺", "康师傅"],
         "features": ["夹心", "酥脆", "分享装", "早餐", "经典"], "specs": ["袋装", "盒装", "家庭装"]},
        {"noun": "牛奶乳品", "price": (29, 199), "brands": ["蒙牛", "伊利", "光明", "三元", "新希望"],
         "features": ["纯牛奶", "有机", "高钙", "早餐奶", "低脂"], "specs": ["12盒", "24盒"]},
        {"noun": "方便速食", "price": (9, 99), "brands": ["康师傅", "统一", "今麦郎", "白象", "日清", "海底捞", "自嗨锅", "莫小仙", "拉面说"],
         "features": ["方便", "红烧", "麻辣", "自热", "非油炸"], "specs": ["单盒", "5盒装", "整箱"]},
        {"noun": "膨化食品", "price": (5, 59), "brands": ["乐事", "旺旺", "奥利奥"],
         "features": ["薯片", "原味", "分享装", "香辣", "酥脆"], "specs": ["袋装", "桶装"]},
    ],
    "家居": [
        # 关键修复：把纸品 / 厨电 / 清洁电器 / 收纳 / 家具彻底分开，各自独立价格区间
        {"noun": "抽纸卷纸", "price": (15, 129), "brands": ["维达", "清风", "心相印", "洁柔", "得宝"],
         "features": ["3层", "亲肤", "整箱", "原生木浆", "大规格"], "specs": ["6包", "18包", "整箱"]},
        {"noun": "厨房小家电", "price": (99, 1999), "brands": ["苏泊尔", "美的", "九阳", "小熊", "摩飞", "飞利浦", "松下"],
         "features": ["多功能", "易清洗", "大容量", "智能", "静音"], "specs": ["标准款", "升级款"]},
        {"noun": "清洁电器", "price": (699, 5999), "brands": ["戴森", "科沃斯", "石头", "美的", "飞利浦"],
         "features": ["大吸力", "自动回充", "长续航", "静音", "除螨"], "specs": ["标准版", "旗舰版"]},
        {"noun": "收纳日用", "price": (9, 199), "brands": ["宜家", "网易严选", "小米有品", "名创优品", "无印良品"],
         "features": ["收纳", "简约", "耐用", "环保", "多功能"], "specs": ["单个", "套装"]},
        {"noun": "家具", "price": (299, 4999), "brands": ["全友", "顾家", "林氏木业", "源氏木语", "原始原素", "宜家"],
         "features": ["实木", "简约", "环保", "耐用", "北欧"], "specs": ["单件", "组合"]},
    ],
    "母婴": [
        {"noun": "纸尿裤", "price": (39, 299), "brands": ["帮宝适", "好奇", "花王", "大王", "尤妮佳", "Babycare"],
         "features": ["超薄", "透气", "干爽", "夜用", "拉拉裤"], "specs": ["S码", "M码", "L码", "XL码"]},
        {"noun": "婴儿奶粉", "price": (129, 599), "brands": ["飞鹤", "伊利金领冠", "君乐宝", "美赞臣", "惠氏"],
         "features": ["含DHA", "益生菌", "接近母乳", "易吸收", "分段"], "specs": ["1段", "2段", "3段"]},
        {"noun": "婴儿洗护", "price": (29, 199), "brands": ["贝亲", "新安怡", "可么多么", "Babycare", "全棉时代", "子初"],
         "features": ["温和", "无泪配方", "保湿", "天然", "敏感肌"], "specs": ["单瓶", "套装"]},
        {"noun": "儿童服饰", "price": (39, 299), "brands": ["巴拉巴拉", "安奈儿", "小猪班纳", "童泰", "英氏"],
         "features": ["纯棉", "柔软", "透气", "卡通", "亲肤"], "specs": ["90cm", "100cm", "110cm", "120cm"]},
        {"noun": "婴儿推车", "price": (299, 2999), "brands": ["好孩子", "Babycare", "贝亲"],
         "features": ["可折叠", "轻便", "减震", "双向", "高景观"], "specs": ["标准款", "高配款"]},
    ],
    "运动": [
        {"noun": "跑步鞋", "price": (199, 1299), "brands": ["Nike", "Adidas", "李宁", "安踏", "特步", "361°", "匹克", "Under Armour"],
         "features": ["缓震", "回弹", "透气", "轻量", "碳板"], "specs": ["40", "41", "42", "43"]},
        {"noun": "瑜伽用品", "price": (39, 599), "brands": ["Lululemon", "迪卡侬", "Keep", "Nike"],
         "features": ["防滑", "加厚", "环保", "便携", "回弹"], "specs": ["单件", "套装"]},
        {"noun": "健身器材", "price": (99, 2999), "brands": ["迪卡侬", "Keep", "李宁"],
         "features": ["家用", "折叠", "静音", "多功能", "稳固"], "specs": ["入门版", "进阶版"]},
        {"noun": "运动手表", "price": (149, 3999), "brands": ["小米手环", "华为手表", "佳明", "松拓", "Keep"],
         "features": ["心率", "GPS", "血氧", "多运动模式", "长续航"], "specs": ["标准版", "旗舰版"]},
        {"noun": "球类装备", "price": (29, 999), "brands": ["威尔胜", "尤尼克斯", "红双喜", "双鱼", "迪卡侬"],
         "features": ["耐磨", "手感", "专业", "训练", "比赛"], "specs": ["标准", "专业级"]},
        {"noun": "冲锋衣", "price": (299, 2999), "brands": ["Columbia", "The North Face", "迪卡侬", "Nike"],
         "features": ["防风", "防水", "透气", "保暖", "户外"], "specs": ["M", "L", "XL"]},
    ],
}

PLATFORMS = ["jd", "tb", "pdd"]

# Promotions gated by price threshold (assigned only when price qualifies)
PROMOTIONS = [
    {"id": "promo_01", "min_price": 100},   # 满100减20
    {"id": "promo_02", "min_price": 299},   # 满299减50
    {"id": "promo_03", "min_price": 0},     # 买3件打7折
    {"id": "promo_04", "min_price": 0},     # 买2件打85折
]

# Temporary: all products share one placeholder image. Swap for real per-SKU
# images (or restore the seeded picsum URL) when a real catalog is available.
PLACEHOLDER_IMAGE = "https://picsum.photos/id/21/400/400"


def _sample_price(low: float, high: float, rng: random.Random) -> float:
    """Skew toward the lower end for realism (most SKUs are affordable)."""
    mode = low + (high - low) * 0.3
    return round(rng.triangular(low, high, mode), 2)


def _pick_promo(price: float, rng: random.Random) -> str | None:
    """~55% of products get a price-appropriate promotion."""
    if rng.random() > 0.55:
        return None
    eligible = [p["id"] for p in PROMOTIONS if price >= p["min_price"]]
    return rng.choice(eligible) if eligible else None


def generate_products(count: int = 5000, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    products: list[dict] = []

    for category, subtypes in CATALOG.items():
        for sub in subtypes:
            noun = sub["noun"]
            low, high = sub["price"]
            for brand in sub["brands"]:
                # 2 variants per brand-subtype, each with a random spec
                for _ in range(2):
                    spec = rng.choice(sub["specs"])
                    price = _sample_price(low, high, rng)
                    features = rng.sample(sub["features"], k=min(3, len(sub["features"])))

                    # rating: brand baseline 3.9~5.0, skewed high for demo quality
                    rating = round(rng.triangular(3.9, 5.0, 4.6), 1)
                    stock = 0 if rng.random() < 0.05 else rng.randint(10, 800)
                    delivery = rng.randint(15, 120)

                    name = f"{brand} {noun} {spec}"
                    embedding_text = f"{category} {noun} {brand} {' '.join(features)}"

                    # per-platform SKUs (1-3 platforms), price varies slightly
                    for plat in rng.sample(PLATFORMS, rng.randint(1, 3)):
                        plat_price = round(price * rng.uniform(0.92, 1.08), 2)
                        products.append({
                            "product_id": "",  # assigned after trim
                            "name": name,
                            "category": category,
                            "product_type": noun,
                            "brand": brand,
                            "price": plat_price,
                            "stock": stock,
                            "platform_id": plat,
                            "promotion_id": _pick_promo(plat_price, rng),
                            "features": features,
                            "rating": rating,
                            "delivery_minutes": delivery,
                            "embedding_text": embedding_text,
                            "image_url": "",  # assigned after id
                        })

    rng.shuffle(products)
    if len(products) > count:
        products = products[:count]
    else:
        # pad by duplicating (with new spec-less clones) if catalog is small
        while len(products) < count and products:
            products.append(dict(rng.choice(products)))

    for i, p in enumerate(products):
        p["product_id"] = f"batch_{i + 1:05d}"
        p["image_url"] = PLACEHOLDER_IMAGE

    return products


def main():
    parser = argparse.ArgumentParser(description="Batch mock product generator (v2)")
    parser.add_argument("--count", type=int, default=5000, help="Number of products")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--output", type=str, default="data/mock_products_5k.json", help="Output path")
    args = parser.parse_args()

    products = generate_products(args.count, args.seed)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    # Stats + sanity report
    cats: dict[str, int] = {}
    subs: dict[str, int] = {}
    price_by_sub: dict[str, list[float]] = {}
    for p in products:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
        subs[p["product_type"]] = subs.get(p["product_type"], 0) + 1
        price_by_sub.setdefault(p["product_type"], []).append(p["price"])

    print(f"Generated {len(products)} products -> {output_path}")
    print(f"Categories: {cats}")
    print("Price range per subtype (min / avg / max):")
    for sub in sorted(price_by_sub):
        prices = price_by_sub[sub]
        avg = sum(prices) / len(prices)
        print(f"  {sub:10s}  ¥{min(prices):>8.2f} / ¥{avg:>8.2f} / ¥{max(prices):>8.2f}  (n={len(prices)})")


if __name__ == "__main__":
    main()
