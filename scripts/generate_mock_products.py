"""Batch mock product generator for evaluation and funnel-trap experiments.

Usage:
    python scripts/generate_mock_products.py [--count 5000] [--seed 42] [--output data/mock_products_5k.json]

Pure Python rule-based generation, zero LLM calls.
Strategy: 8 categories x 25 brands x 5 series x 5 price_ranges = 5000 products.
"""

import argparse
import json
import random
from pathlib import Path

CATEGORIES = {
    "护肤": {
        "brands": [
            "雅诗兰黛", "兰蔻", "SK-II", "资生堂", "欧莱雅",
            "薇诺娜", "珀莱雅", "自然堂", "百雀羚", "玉兰油",
            "科颜氏", "理肤泉", "悦木之源", "黛珂", "雪花秀",
            "后", "赫莲娜", "海蓝之谜", "CPB", "IPSA",
            "倩碧", "娇韵诗", "雅漾", "芙丽芳丝", "珂润",
        ],
        "series": ["经典款", "升级版", "限定版", "套装", "旅行装"],
        "features_pool": ["保湿", "修护", "抗老", "美白", "控油", "舒缓", "紧致", "淡斑", "补水", "防晒"],
        "price_range": (29, 2999),
    },
    "奶茶": {
        "brands": [
            "古茗", "喜茶", "奈雪", "蜜雪冰城", "茶百道",
            "一点点", "CoCo", "沪上阿姨", "书亦烧仙草", "益禾堂",
            "瑞幸", "星巴克", "霸王茶姬", "甜啦啦", "七分甜",
            "茶颜悦色", "柠季", "悸动烧仙草", "吾饮良品", "蜜城之恋",
            "鲜茶亭", "果麦", "快乐柠檬", "贡茶", "85度C",
        ],
        "series": ["大杯", "中杯", "超大杯", "热饮", "冰饮"],
        "features_pool": ["芋泥", "珍珠", "椰果", "芝士", "奶盖", "水果茶", "杨枝甘露", "柠檬", "芒果", "草莓"],
        "price_range": (5, 38),
    },
    "数码": {
        "brands": [
            "Apple", "华为", "小米", "三星", "OPPO",
            "vivo", "荣耀", "一加", "realme", "iQOO",
            "索尼", "Bose", "JBL", "Beats", "铁三角",
            "联想", "戴尔", "惠普", "华硕", "宏碁",
            "大疆", "GoPro", "任天堂", "微软", "谷歌",
        ],
        "series": ["旗舰版", "标准版", "青春版", "Pro", "SE"],
        "features_pool": ["5G", "快充", "长续航", "高刷", "NFC", "防水", "轻薄", "高性能", "大内存", "高清屏"],
        "price_range": (99, 12999),
    },
    "服饰": {
        "brands": [
            "Nike", "Adidas", "优衣库", "ZARA", "H&M",
            "李宁", "安踏", "特步", "361°", "匹克",
            "太平鸟", "森马", "美特斯邦威", "以纯", "海澜之家",
            "波司登", "鄂尔多斯", "恒源祥", "七匹狼", "雅戈尔",
            "UR", "ONLY", "VERO MODA", "JACK & JONES", "GXG",
        ],
        "series": ["基础款", "潮流款", "联名款", "经典款", "运动款"],
        "features_pool": ["纯棉", "透气", "速干", "保暖", "修身", "宽松", "百搭", "时尚", "休闲", "商务"],
        "price_range": (29, 1999),
    },
    "食品": {
        "brands": [
            "三只松鼠", "良品铺子", "百草味", "来伊份", "洽洽",
            "旺旺", "达利园", "徐福记", "奥利奥", "乐事",
            "蒙牛", "伊利", "光明", "三元", "新希望",
            "康师傅", "统一", "今麦郎", "白象", "日清",
            "海底捞", "自嗨锅", "莫小仙", "开小灶", "拉面说",
        ],
        "series": ["家庭装", "分享装", "尝鲜装", "经典口味", "新品"],
        "features_pool": ["零食", "方便速食", "乳制品", "饮料", "坚果", "膨化", "饼干", "糖果", "即食", "冲泡"],
        "price_range": (3, 199),
    },
    "家居": {
        "brands": [
            "宜家", "网易严选", "小米有品", "名创优品", "无印良品",
            "维达", "清风", "心相印", "洁柔", "得宝",
            "苏泊尔", "美的", "九阳", "小熊", "摩飞",
            "飞利浦", "松下", "戴森", "科沃斯", "石头",
            "全友", "顾家", "林氏木业", "源氏木语", "原始原素",
        ],
        "series": ["简约款", "智能款", "经典款", "升级款", "mini"],
        "features_pool": ["收纳", "清洁", "厨房", "卧室", "浴室", "智能", "环保", "耐用", "便携", "多功能"],
        "price_range": (5, 4999),
    },
    "母婴": {
        "brands": [
            "Babycare", "好孩子", "贝亲", "新安怡", "可么多么",
            "帮宝适", "好奇", "花王", "大王", "尤妮佳",
            "飞鹤", "伊利金领冠", "君乐宝", "美赞臣", "惠氏",
            "巴拉巴拉", "安奈儿", "小猪班纳", "童泰", "英氏",
            "B.Duck", "可优比", "十月结晶", "子初", "全棉时代",
        ],
        "series": ["新生儿", "6-12月", "1-3岁", "3-6岁", "6岁以上"],
        "features_pool": ["纸尿裤", "奶粉", "辅食", "玩具", "洗护", "服饰", "推车", "安全座椅", "餐具", "寝具"],
        "price_range": (9, 599),
    },
    "运动": {
        "brands": [
            "Nike", "Adidas", "Under Armour", "Lululemon", "迪卡侬",
            "李宁", "安踏", "特步", "361°", "匹克",
            "Keep", "小米手环", "华为手表", "佳明", "松拓",
            "威尔胜", "尤尼克斯", "李宁体育", "红双喜", "双鱼",
            "Speedo", "Arena", "Decathlon", "Columbia", "The North Face",
        ],
        "series": ["专业版", "入门版", "进阶版", "旗舰版", "轻量版"],
        "features_pool": ["跑步", "健身", "瑜伽", "游泳", "篮球", "足球", "羽毛球", "骑行", "登山", "户外"],
        "price_range": (15, 3999),
    },
}

PLATFORMS = ["jd", "tb", "pdd"]

PROMOTION_IDS = [
    "promo_01",  # 满100减20
    "promo_02",  # 满299减50
    "promo_03",  # 买3件打7折
    "promo_04",  # 买2件打85折
    None,        # no promotion
    None,
    None,
    None,
]


def generate_products(count: int = 5000, seed: int = 42) -> list[dict]:
    random.seed(seed)

    products = []
    product_id = 1

    cat_list = list(CATEGORIES.items())

    # Calculate how many products per combination
    # 8 categories x 25 brands x 5 series x 5 price_bins = 5000
    price_bins = 5

    for cat_name, cat_info in cat_list:
        brands = cat_info["brands"]
        series_list = cat_info["series"]
        features_pool = cat_info["features_pool"]
        price_min, price_max = cat_info["price_range"]

        # Calculate price step for this category
        price_step = (price_max - price_min) / price_bins

        for brand in brands:
            for series in series_list:
                for bin_idx in range(price_bins):
                    # Price: random within the bin, slightly jittered
                    p_low = price_min + bin_idx * price_step
                    p_high = price_min + (bin_idx + 1) * price_step
                    price = round(random.uniform(p_low, p_high), 2)

                    # Features: pick 2-4 random features
                    n_features = random.randint(2, 4)
                    features = random.sample(features_pool, n_features)

                    # Platform: 1-3 platforms per product
                    n_platforms = random.randint(1, 3)
                    platforms = random.sample(PLATFORMS, n_platforms)

                    # Promotion: ~60% have promotion
                    promo_id = random.choice(PROMOTION_IDS)

                    # Rating: 3.0 - 5.0
                    rating = round(random.uniform(3.0, 5.0), 1)

                    # Stock: mostly available, ~5% out of stock
                    stock = 0 if random.random() < 0.05 else random.randint(10, 500)

                    # Delivery: 15-120 minutes
                    delivery = random.randint(15, 120)

                    # Product name
                    name = f"{brand} {series}"

                    # Embedding text
                    embedding_text = f"{cat_name} {brand} {name} {' '.join(features)}"

                    # Create one product per platform
                    for plat in platforms:
                        # Slight price variation across platforms
                        plat_price = round(price * random.uniform(0.9, 1.1), 2)

                        products.append({
                            "product_id": f"batch_{product_id:05d}",
                            "name": name,
                            "category": cat_name,
                            "brand": brand,
                            "price": plat_price,
                            "stock": stock,
                            "platform_id": plat,
                            "promotion_id": promo_id,
                            "features": features,
                            "rating": rating,
                            "delivery_minutes": delivery,
                            "embedding_text": embedding_text,
                        })
                        product_id += 1

    # Trim or pad to exact count
    if len(products) > count:
        random.shuffle(products)
        products = products[:count]
    elif len(products) < count:
        # Shouldn't happen with 8x25x5x5=5000 base, but just in case
        while len(products) < count:
            products.append(random.choice(products))

    # Re-number after trimming
    for i, p in enumerate(products):
        p["product_id"] = f"batch_{i+1:05d}"

    return products


def main():
    parser = argparse.ArgumentParser(description="Batch mock product generator")
    parser.add_argument("--count", type=int, default=5000, help="Number of products")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--output", type=str, default="data/mock_products_5k.json", help="Output path")
    args = parser.parse_args()

    products = generate_products(args.count, args.seed)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    # Stats
    cats = {}
    plats = {}
    for p in products:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
        plats[p["platform_id"]] = plats.get(p["platform_id"], 0) + 1

    print(f"Generated {len(products)} products -> {output_path}")
    print(f"Categories: {cats}")
    print(f"Platforms: {plats}")


if __name__ == "__main__":
    main()
