"""Generate 50 mock clothing products for the shopping agent.

Output: data/mock_data.json
Format: compatible with existing product schema (product_id, name, category, brand, price, stock, platform_id, promotion_id, features, embedding_text, reputation, image_url)
"""

import json
import random

random.seed(42)

# 男装上装
MEN_TOPS = [
    ("简约纯棉圆领T恤", "棉", ["透气", "百搭", "纯棉", "圆领"], 59, 129),
    ("商务免烫长袖衬衫", "棉", ["免烫", "商务", "长袖", "通勤"], 159, 299),
    ("日系休闲亚麻短袖衬衫", "亚麻", ["透气", "休闲", "亚麻", "短袖"], 129, 239),
    ("美式复古印花圆领T恤", "棉", ["复古", "印花", "潮流", "圆领"], 79, 159),
    ("轻薄防晒皮肤衣", "尼龙", ["防晒", "轻薄", "户外", "速干"], 199, 359),
    ("春秋纯色连帽卫衣", "棉", ["连帽", "休闲", "百搭", "春秋"], 149, 269),
    ("冬季加厚羽绒服", "羽绒", ["保暖", "羽绒", "冬季", "加厚"], 499, 899),
    ("商务休闲夹克外套", "聚酯纤维", ["商务", "休闲", "夹克", "春秋"], 259, 459),
    ("运动速干跑步背心", "聚酯纤维", ["速干", "运动", "轻便", "跑步"], 49, 99),
    ("经典翻领Polo衫", "棉珠地", ["Polo", "商务休闲", "翻领", "夏季"], 119, 219),
    ("潮流扎染宽松T恤", "棉", ["扎染", "潮流", "宽松", "街头"], 89, 169),
    ("冬季加绒保暖内衣套装", "棉", ["保暖", "加绒", "冬季", "内衣"], 129, 229),
]

# 男装下装
MEN_BOTTOMS = [
    ("修身弹力牛仔裤", "牛仔", ["修身", "弹力", "牛仔", "百搭"], 159, 299),
    ("商务直筒西裤", "聚酯纤维", ["商务", "直筒", "西裤", "通勤"], 189, 339),
    ("运动束脚休闲裤", "棉", ["束脚", "运动", "休闲", "舒适"], 129, 239),
    ("夏季薄款五分短裤", "棉", ["短裤", "夏季", "薄款", "休闲"], 79, 149),
    ("工装多口袋长裤", "棉", ["工装", "多口袋", "潮流", "耐磨"], 169, 289),
    ("弹力修身小脚裤", "棉弹", ["修身", "小脚", "弹力", "显瘦"], 139, 259),
    ("春秋休闲卡其裤", "棉", ["卡其裤", "休闲", "春秋", "百搭"], 149, 269),
    ("运动篮球七分裤", "聚酯纤维", ["篮球", "运动", "七分", "速干"], 89, 169),
]

# 女装上装
WOMEN_TOPS = [
    ("法式碎花雪纺衬衫", "雪纺", ["法式", "碎花", "雪纺", "气质"], 129, 239),
    ("甜美蕾丝拼接针织衫", "针织", ["蕾丝", "甜美", "针织", "春秋"], 149, 269),
    ("简约纯色V领T恤", "棉", ["V领", "简约", "百搭", "纯棉"], 59, 119),
    ("气质通勤小西装外套", "聚酯纤维", ["通勤", "小西装", "气质", "职场"], 299, 499),
    ("温柔风针织开衫", "针织", ["温柔风", "开衫", "针织", "百搭"], 169, 299),
    ("短款修身牛仔外套", "牛仔", ["短款", "修身", "牛仔", "百搭"], 199, 349),
    ("宽松BF风印花T恤", "棉", ["BF风", "宽松", "印花", "潮流"], 79, 149),
    ("轻奢真丝吊带背心", "真丝", ["真丝", "吊带", "轻奢", "夏季"], 199, 399),
    ("冬季白鸭绒羽绒服", "羽绒", ["羽绒", "保暖", "冬季", "白鸭绒"], 599, 999),
    ("学院风百褶短款上衣", "聚酯纤维", ["学院风", "百褶", "短款", "减龄"], 119, 219),
    ("法式方领泡泡袖上衣", "棉", ["法式", "方领", "泡泡袖", "复古"], 139, 249),
    ("运动瑜伽紧身短袖", "氨纶", ["运动", "瑜伽", "紧身", "速干"], 69, 139),
]

# 女装下装
WOMEN_BOTTOMS = [
    ("高腰A字半身裙", "聚酯纤维", ["高腰", "A字", "半身裙", "显瘦"], 139, 259),
    ("修身弹力小脚牛仔裤", "牛仔", ["修身", "弹力", "牛仔", "显瘦"], 159, 289),
    ("甜美碎花雪纺长裙", "雪纺", ["碎花", "雪纺", "长裙", "甜美"], 149, 269),
    ("高腰阔腿休闲裤", "棉", ["高腰", "阔腿", "休闲", "显瘦"], 129, 239),
    ("气质通勤九分西裤", "聚酯纤维", ["通勤", "九分", "西裤", "职场"], 169, 299),
    ("运动紧身瑜伽裤", "氨纶", ["瑜伽", "紧身", "运动", "高弹"], 89, 169),
    ("冬季加厚毛呢阔腿裤", "毛呢", ["毛呢", "阔腿", "冬季", "加厚"], 199, 349),
    ("百搭高腰直筒牛仔裤", "牛仔", ["高腰", "直筒", "牛仔", "百搭"], 149, 269),
    ("夏季薄款防晒长裤", "冰丝", ["防晒", "薄款", "夏季", "冰丝"], 99, 189),
    ("包臀显瘦鱼尾裙", "聚酯纤维", ["包臀", "鱼尾裙", "显瘦", "气质"], 159, 279),
]

EXTRA_ITEMS = [
    ("男装/上装/外套", "春秋棒球领夹克", "聚酯纤维", ["棒球领", "夹克", "休闲", "春秋"], 189, 329),
    ("男装/上装/针织", "纯色V领羊毛衫", "羊毛", ["V领", "羊毛", "保暖", "冬季"], 199, 359),
    ("男装/下装/短裤", "休闲运动中裤", "棉", ["运动", "休闲", "中裤", "夏季"], 89, 169),
    ("男装/下装/裤装", "弹力修身休闲裤", "棉弹", ["修身", "弹力", "休闲", "百搭"], 139, 249),
    ("女装/上装/卫衣", "宽松连帽字母卫衣", "棉", ["连帽", "字母", "宽松", "减龄"], 139, 249),
    ("女装/上装/针织", "短款修身打底衫", "针织", ["短款", "修身", "打底", "百搭"], 79, 149),
    ("女装/下装/裙装", "高腰百褶A字短裙", "聚酯纤维", ["高腰", "百褶", "短裙", "学院风"], 109, 199),
    ("女装/下装/裤装", "冬季加绒紧身牛仔裤", "牛仔", ["加绒", "紧身", "牛仔", "冬季保暖"], 169, 299),
]

BRANDS = ["优衣库", "ZARA", "H&M", "海澜之家", "太平鸟", "GXG", "ONLY", "VERO MODA", "杰克琼斯", "森马", "美特斯邦威", "以纯"]
PLATFORMS = ["jd", "tb", "pdd"]
PROMOTIONS = ["promo_01", "promo_02", "promo_03", None, None, None]  # ~50% have promo


def build_embedding_text(name: str, brand: str, category: str, features: list[str]) -> str:
    return f"{category} {brand} {name} {' '.join(features)}"


def generate_products() -> list[dict]:
    all_items = []

    for name, material, features, price_min, price_max in MEN_TOPS:
        all_items.append(("男装/上装/T恤衬衫", name, material, features, price_min, price_max))
    for name, material, features, price_min, price_max in MEN_BOTTOMS:
        all_items.append(("男装/下装/裤装", name, material, features, price_min, price_max))
    for name, material, features, price_min, price_max in WOMEN_TOPS:
        all_items.append(("女装/上装/衬衫外套", name, material, features, price_min, price_max))
    for name, material, features, price_min, price_max in WOMEN_BOTTOMS:
        all_items.append(("女装/下装/裙裤", name, material, features, price_min, price_max))

    for category, name, material, features, price_min, price_max in EXTRA_ITEMS:
        all_items.append((category, name, material, features, price_min, price_max))

    products = []
    for i, (category, name, material, features, price_min, price_max) in enumerate(all_items, 1):
        brand = random.choice(BRANDS)
        price = random.randint(price_min, price_max)
        stock = random.randint(20, 500)
        platform = random.choice(PLATFORMS)
        promo = random.choice(PROMOTIONS)
        reputation = round(random.uniform(0.55, 0.98), 2)

        features_with_material = [material] + features

        product = {
            "product_id": f"prod_{i:03d}",
            "name": f"{brand} {name}",
            "image_url": "",
            "category": category,
            "brand": brand,
            "price": price,
            "stock": stock,
            "platform_id": platform,
            "promotion_id": promo,
            "features": features_with_material,
            "embedding_text": build_embedding_text(name, brand, category, features_with_material),
            "reputation": reputation,
        }
        products.append(product)

    return products


def main():
    products = generate_products()
    data = {"products": products}
    with open("data/mock_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Generated {len(products)} clothing products → data/mock_data.json")

    # Stats
    cats = {}
    for p in products:
        cats[p["category"]] = cats.get(p["category"], 0) + 1
    for cat, count in cats.items():
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
