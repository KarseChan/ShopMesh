"""Generate clothing mock data with proper product_type and clean categories.

Changes from old mock_data:
- category: "男装/上装" (no mixed leaf like "T恤衬衫")
- product_type: "衬衫" / "T恤" / "Polo衫" / "背心" etc.
- embedding_text: uses product_type, NOT the old mixed category
"""

import json
import random
from pathlib import Path

random.seed(42)

BRANDS = {
    "男装": ["H&M", "ONLY", "优衣库", "森马", "美特斯邦威", "杰克琼斯", "GXG", "海澜之家", "ZARA", "以纯"],
    "女装": ["H&M", "ONLY", "森马", "杰克琼斯", "美特斯邦威", "太平鸟", "GXG", "海澜之家", "VERO MODA"],
}

PLATFORMS = ["jd", "tb", "pdd"]

PRODUCTS = [
    # === 男装/上装 ===
    # T恤
    {"name": "美特斯邦威 简约纯棉圆领T恤", "product_type": "T恤", "brand": "美特斯邦威",
     "price": 73, "features": ["棉", "透气", "百搭", "纯棉", "圆领"], "reputation": 0.66},
    {"name": "森马 美式复古印花圆领T恤", "product_type": "T恤", "brand": "森马",
     "price": 82, "features": ["棉", "复古", "印花", "潮流", "圆领"], "reputation": 0.83},
    {"name": "GXG 潮流扎染宽松T恤", "product_type": "T恤", "brand": "GXG",
     "price": 162, "features": ["棉", "扎染", "潮流", "宽松", "街头"], "reputation": 0.57},
    # 衬衫
    {"name": "H&M 商务免烫长袖衬衫", "product_type": "衬衫", "brand": "H&M",
     "price": 185, "features": ["棉", "免烫", "商务", "长袖", "通勤"], "reputation": 0.59},
    {"name": "ONLY 日系休闲亚麻短袖衬衫", "product_type": "衬衫", "brand": "ONLY",
     "price": 133, "features": ["亚麻", "透气", "休闲", "短袖"], "reputation": 0.65},
    {"name": "海澜之家 商务正装白色衬衫", "product_type": "衬衫", "brand": "海澜之家",
     "price": 159, "features": ["棉", "正装", "白色", "长袖", "免烫"], "reputation": 0.72},
    # Polo衫
    {"name": "ONLY 经典翻领Polo衫", "product_type": "Polo衫", "brand": "ONLY",
     "price": 129, "features": ["棉珠地", "Polo", "商务休闲", "翻领", "夏季"], "reputation": 0.82},
    {"name": "优衣库 纯色珠地Polo衫", "product_type": "Polo衫", "brand": "优衣库",
     "price": 99, "features": ["棉珠地", "纯色", "休闲", "翻领", "百搭"], "reputation": 0.78},
    # 背心
    {"name": "优衣库 运动速干跑步背心", "product_type": "背心", "brand": "优衣库",
     "price": 95, "features": ["聚酯纤维", "速干", "运动", "轻便", "跑步"], "reputation": 0.71},
    # 卫衣
    {"name": "优衣库 春秋纯色连帽卫衣", "product_type": "卫衣", "brand": "优衣库",
     "price": 246, "features": ["棉", "连帽", "休闲", "百搭", "春秋"], "reputation": 0.85},
    {"name": "杰克琼斯 宽松连帽字母卫衣", "product_type": "卫衣", "brand": "杰克琼斯",
     "price": 289, "features": ["棉", "连帽", "字母", "宽松", "潮流"], "reputation": 0.73},
    # 外套
    {"name": "太平鸟 冬季加厚羽绒服", "product_type": "羽绒服", "brand": "太平鸟",
     "price": 578, "features": ["羽绒", "保暖", "冬季", "加厚"], "reputation": 0.59},
    {"name": "ZARA 商务休闲夹克外套", "product_type": "夹克", "brand": "ZARA",
     "price": 350, "features": ["聚酯纤维", "商务", "休闲", "夹克", "春秋"], "reputation": 0.66},
    {"name": "杰克琼斯 轻薄防晒皮肤衣", "product_type": "皮肤衣", "brand": "杰克琼斯",
     "price": 306, "features": ["尼龙", "防晒", "轻薄", "户外", "速干"], "reputation": 0.67},
    {"name": "以纯 春秋棒球领夹克", "product_type": "夹克", "brand": "以纯",
     "price": 199, "features": ["棉", "棒球领", "休闲", "春秋", "百搭"], "reputation": 0.61},
    # 针织
    {"name": "ZARA 纯色V领羊毛衫", "product_type": "针织衫", "brand": "ZARA",
     "price": 299, "features": ["羊毛", "V领", "纯色", "商务", "秋冬"], "reputation": 0.70},
    # 内衣
    {"name": "海澜之家 冬季加绒保暖内衣套装", "product_type": "内衣", "brand": "海澜之家",
     "price": 227, "features": ["棉", "保暖", "加绒", "冬季", "内衣"], "reputation": 0.92},

    # === 男装/下装 ===
    {"name": "ONLY 修身弹力牛仔裤", "product_type": "牛仔裤", "brand": "ONLY",
     "price": 299, "features": ["牛仔", "修身", "弹力", "百搭", "四季"], "reputation": 0.72},
    {"name": "GXG 商务直筒西裤", "product_type": "西裤", "brand": "GXG",
     "price": 329, "features": ["聚酯纤维", "直筒", "商务", "西裤", "通勤"], "reputation": 0.68},
    {"name": "美特斯邦威 运动束脚休闲裤", "product_type": "休闲裤", "brand": "美特斯邦威",
     "price": 129, "features": ["棉", "束脚", "运动", "休闲", "百搭"], "reputation": 0.60},
    {"name": "海澜之家 夏季薄款五分短裤", "product_type": "短裤", "brand": "海澜之家",
     "price": 109, "features": ["棉", "五分", "薄款", "夏季", "休闲"], "reputation": 0.75},
    {"name": "美特斯邦威 工装多口袋长裤", "product_type": "休闲裤", "brand": "美特斯邦威",
     "price": 159, "features": ["棉", "工装", "多口袋", "潮流", "长裤"], "reputation": 0.58},
    {"name": "优衣库 弹力修身小脚裤", "product_type": "休闲裤", "brand": "优衣库",
     "price": 199, "features": ["棉", "弹力", "修身", "小脚", "百搭"], "reputation": 0.80},
    {"name": "ZARA 春秋休闲卡其裤", "product_type": "卡其裤", "brand": "ZARA",
     "price": 259, "features": ["棉", "卡其", "休闲", "春秋", "直筒"], "reputation": 0.65},
    {"name": "美特斯邦威 运动篮球七分裤", "product_type": "短裤", "brand": "美特斯邦威",
     "price": 89, "features": ["涤纶", "篮球", "七分", "运动", "透气"], "reputation": 0.55},
    {"name": "ONLY 弹力修身休闲裤", "product_type": "休闲裤", "brand": "ONLY",
     "price": 239, "features": ["棉", "弹力", "修身", "休闲", "百搭"], "reputation": 0.70},
    {"name": "以纯 休闲运动中裤", "product_type": "短裤", "brand": "以纯",
     "price": 79, "features": ["涤纶", "运动", "中裤", "休闲", "夏季"], "reputation": 0.58},

    # === 女装/上装 ===
    {"name": "H&M 法式碎花雪纺衬衫", "product_type": "衬衫", "brand": "H&M",
     "price": 160, "features": ["雪纺", "法式", "碎花", "气质"], "reputation": 0.66},
    {"name": "H&M 简约纯色V领T恤", "product_type": "T恤", "brand": "H&M",
     "price": 91, "features": ["棉", "V领", "简约", "百搭", "纯棉"], "reputation": 0.92},
    {"name": "森马 甜美蕾丝拼接针织衫", "product_type": "针织衫", "brand": "森马",
     "price": 203, "features": ["针织", "蕾丝", "甜美", "春秋"], "reputation": 0.71},
    {"name": "H&M 气质通勤小西装外套", "product_type": "西装", "brand": "H&M",
     "price": 459, "features": ["聚酯纤维", "通勤", "小西装", "气质", "职场"], "reputation": 0.81},
    {"name": "ONLY 温柔风针织开衫", "product_type": "针织衫", "brand": "ONLY",
     "price": 266, "features": ["针织", "温柔风", "开衫", "百搭"], "reputation": 0.66},
    {"name": "杰克琼斯 短款修身牛仔外套", "product_type": "牛仔外套", "brand": "杰克琼斯",
     "price": 201, "features": ["牛仔", "短款", "修身", "百搭"], "reputation": 0.84},
    {"name": "杰克琼斯 宽松BF风印花T恤", "product_type": "T恤", "brand": "杰克琼斯",
     "price": 113, "features": ["棉", "BF风", "宽松", "印花", "潮流"], "reputation": 0.60},
    {"name": "ONLY 轻奢真丝吊带背心", "product_type": "背心", "brand": "ONLY",
     "price": 239, "features": ["真丝", "吊带", "轻奢", "夏季"], "reputation": 0.93},
    {"name": "太平鸟 冬季白鸭绒羽绒服", "product_type": "羽绒服", "brand": "太平鸟",
     "price": 855, "features": ["羽绒", "保暖", "冬季", "白鸭绒"], "reputation": 0.94},
    {"name": "美特斯邦威 学院风百褶短款上衣", "product_type": "上衣", "brand": "美特斯邦威",
     "price": 157, "features": ["聚酯纤维", "学院风", "百褶", "短款", "减龄"], "reputation": 0.81},
    {"name": "H&M 法式方领泡泡袖上衣", "product_type": "上衣", "brand": "H&M",
     "price": 186, "features": ["棉", "法式", "方领", "泡泡袖", "复古"], "reputation": 0.96},
    {"name": "杰克琼斯 运动瑜伽紧身短袖", "product_type": "T恤", "brand": "杰克琼斯",
     "price": 69, "features": ["氨纶", "运动", "瑜伽", "紧身", "速干"], "reputation": 0.56},

    # === 女装/下装 ===
    {"name": "GXG 高腰A字半身裙", "product_type": "半身裙", "brand": "GXG",
     "price": 199, "features": ["聚酯纤维", "高腰", "A字", "百搭", "通勤"], "reputation": 0.72},
    {"name": "森马 修身弹力小脚牛仔裤", "product_type": "牛仔裤", "brand": "森马",
     "price": 169, "features": ["牛仔", "修身", "弹力", "小脚", "百搭"], "reputation": 0.68},
    {"name": "杰克琼斯 甜美碎花雪纺长裙", "product_type": "长裙", "brand": "杰克琼斯",
     "price": 189, "features": ["雪纺", "碎花", "甜美", "长裙", "夏季"], "reputation": 0.75},
    {"name": "杰克琼斯 高腰阔腿休闲裤", "product_type": "休闲裤", "brand": "杰克琼斯",
     "price": 179, "features": ["棉", "高腰", "阔腿", "休闲", "百搭"], "reputation": 0.62},
    {"name": "海澜之家 气质通勤九分西裤", "product_type": "西裤", "brand": "海澜之家",
     "price": 249, "features": ["聚酯纤维", "九分", "通勤", "西裤", "气质"], "reputation": 0.80},
    {"name": "美特斯邦威 运动紧身瑜伽裤", "product_type": "瑜伽裤", "brand": "美特斯邦威",
     "price": 99, "features": ["氨纶", "紧身", "运动", "瑜伽", "高腰"], "reputation": 0.55},
    {"name": "海澜之家 冬季加厚毛呢阔腿裤", "product_type": "阔腿裤", "brand": "海澜之家",
     "price": 329, "features": ["毛呢", "加厚", "冬季", "阔腿", "保暖"], "reputation": 0.78},
    {"name": "森马 百搭高腰直筒牛仔裤", "product_type": "牛仔裤", "brand": "森马",
     "price": 189, "features": ["牛仔", "高腰", "直筒", "百搭", "四季"], "reputation": 0.70},
    {"name": "海澜之家 夏季薄款防晒长裤", "product_type": "休闲裤", "brand": "海澜之家",
     "price": 139, "features": ["涤纶", "薄款", "防晒", "夏季", "长裤"], "reputation": 0.65},
    {"name": "海澜之家 包臀显瘦鱼尾裙", "product_type": "鱼尾裙", "brand": "海澜之家",
     "price": 219, "features": ["聚酯纤维", "包臀", "显瘦", "鱼尾", "通勤"], "reputation": 0.73},
    {"name": "VERO MODA 冬季加绒紧身牛仔裤", "product_type": "牛仔裤", "brand": "VERO MODA",
     "price": 279, "features": ["牛仔", "加绒", "紧身", "冬季", "保暖"], "reputation": 0.76},
]


def make_category(gender: str, part: str) -> str:
    return f"{gender}/{part}"


def make_embedding_text(product: dict, category: str) -> str:
    """Build embedding_text: category + product_type + name + features.
    No mixed leaf category like 'T恤衬衫'.
    """
    parts = [category, product["product_type"], product["name"]]
    parts.extend(product["features"])
    return " ".join(parts)


def generate():
    products = []
    pid = 1

    for p in PRODUCTS:
        name = p["name"]
        # Determine gender from product position in list
        if pid <= 17:
            gender = "男装"
            part = "上装"
        elif pid <= 27:
            gender = "男装"
            part = "下装"
        elif pid <= 39:
            gender = "女装"
            part = "上装"
        else:
            gender = "女装"
            part = "下装"

        category = make_category(gender, part)
        product = {
            "product_id": f"prod_{pid:03d}",
            "name": name,
            "image_url": "",
            "category": category,
            "product_type": p["product_type"],
            "brand": p["brand"],
            "price": p["price"],
            "stock": random.randint(10, 999),
            "platform_id": random.choice(PLATFORMS),
            "promotion_id": None,
            "features": p["features"],
            "embedding_text": make_embedding_text(p, category),
            "reputation": p["reputation"],
        }
        products.append(product)
        pid += 1

    return {"products": products}


if __name__ == "__main__":
    data = generate()
    out_path = Path(__file__).resolve().parent.parent / "data" / "mock_data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Generated {len(data['products'])} products → {out_path}")
