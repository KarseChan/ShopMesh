"""推荐质量 Eval Harness —— 量化检索+过滤+排序的推荐质量。

直接喂结构化实体调 product_search(不经 LLM,避开网关波动),对每条 query 检查:
- non_empty:是否有结果
- budget:所有结果在 [price_min, price_max] 内
- product_type:所有结果 product_type 匹配(若指定)
- category:所有结果 category 匹配(若指定)
- scenario:礼物场景不含被排除品类

用法:PYTHONPATH=. python scripts/eval_recommendation.py
依赖:Qdrant + Ollama(embedding)。

设计意图:大多数项目只有功能没有度量。这里用可复现的指标驱动改进,
且直接复用 P0-2 修好的过滤链路作为被测对象。
"""

import asyncio
import sys
import warnings

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# (name, entities, semantic_query, expect)
EVAL_SET = [
    ("预算跑步鞋", {"category": "运动", "product_type": "跑步鞋", "price_max": 800},
     "轻便透气跑步鞋", {"budget_max": 800, "product_type": "跑步鞋"}),
    ("护肤面霜300", {"category": "护肤", "product_type": "面霜", "price_max": 300},
     "保湿面霜", {"budget_max": 300, "product_type": "面霜", "category": "护肤"}),
    ("数码手机5000内", {"category": "数码", "product_type": "智能手机", "price_max": 5000},
     "性价比智能手机", {"budget_max": 5000, "product_type": "智能手机"}),
    ("母婴纸尿裤", {"category": "母婴", "product_type": "纸尿裤"},
     "透气纸尿裤", {"product_type": "纸尿裤"}),
    ("笔记本区间", {"category": "数码", "product_type": "笔记本电脑", "price_min": 4000, "price_max": 9000},
     "轻薄笔记本", {"budget_min": 4000, "budget_max": 9000, "product_type": "笔记本电脑"}),
    ("连衣裙女", {"category": "服饰", "product_type": "连衣裙"},
     "显瘦连衣裙", {"product_type": "连衣裙"}),
    ("坚果零食预算100", {"category": "食品", "product_type": "坚果零食", "price_max": 100},
     "每日坚果", {"budget_max": 100, "product_type": "坚果零食"}),
    ("生日礼物排除饮品", {"scenario": "生日礼物", "price_max": 1000},
     "送女朋友的生日礼物", {"budget_max": 1000, "exclude_categories": ["奶茶", "食品", "家居"]}),
    ("情人节礼物", {"scenario": "情人节", "price_max": 800},
     "送女朋友情人节礼物", {"budget_max": 800, "exclude_categories": ["奶茶", "食品", "家居", "运动"]}),
    ("抽纸预算60", {"category": "家居", "product_type": "抽纸卷纸", "price_max": 60},
     "抽纸", {"budget_max": 60, "product_type": "抽纸卷纸"}),
]


def _check(products, expect):
    """返回各指标 bool(None 表示该 query 不适用该指标)。"""
    res = {}
    res["non_empty"] = len(products) > 0
    prices = [p.get("price") for p in products if p.get("price") is not None]
    bmax, bmin = expect.get("budget_max"), expect.get("budget_min")
    if bmax is not None or bmin is not None:
        res["budget"] = all(
            (bmax is None or pr <= bmax) and (bmin is None or pr >= bmin) for pr in prices
        ) if prices else False
    if expect.get("product_type"):
        pt = expect["product_type"]
        res["product_type"] = all(p.get("product_type") == pt for p in products) if products else False
    if expect.get("category"):
        cat = expect["category"]
        res["category"] = all(p.get("category") == cat for p in products) if products else False
    if expect.get("exclude_categories"):
        ex = set(expect["exclude_categories"])
        res["scenario"] = all(p.get("category") not in ex for p in products) if products else True
    return res


async def main():
    from src.tools.product_search import product_search

    rows = []
    agg = {}
    for name, entities, query, expect in EVAL_SET:
        r = await product_search(entities=entities, semantic_query=query, top_k=30, max_results=10)
        products = r.get("_full_products", r.get("results", []))
        checks = _check(products, expect)
        rows.append((name, len(products), checks))
        for k, v in checks.items():
            agg.setdefault(k, []).append(v)

    print("\n" + "=" * 72)
    print("推荐质量 Eval 报告")
    print("=" * 72)
    for name, n, checks in rows:
        marks = "  ".join(f"{k}={'✓' if v else '✗'}" for k, v in checks.items())
        print(f"  {name:16s} n={n:<3d} {marks}")

    print("-" * 72)
    print("各指标通过率:")
    for k in ("non_empty", "budget", "product_type", "category", "scenario"):
        if k in agg:
            vals = agg[k]
            rate = sum(1 for x in vals if x) / len(vals)
            print(f"  {k:14s} {rate*100:5.1f}%  ({sum(vals)}/{len(vals)})")
    overall = [v for vals in agg.values() for v in vals]
    print("-" * 72)
    print(f"  总体通过率     {sum(overall)/len(overall)*100:5.1f}%  ({sum(overall)}/{len(overall)})")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
