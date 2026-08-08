"""CLI verification for the 秒送 就近检索 link (P1 slice 1a).

自然语言约束 → 规则抽取 → nearby_merchant_search → 时效优先排序,全程无 LLM。
This doubles as the eval/regression harness for the retrieval tool.

Prereqs: docker-compose.infra.yml up (Qdrant 16336 + Ollama bge-m3 11434), and
    python scripts/generate_mock_merchants.py
    python scripts/build_merchant_index.py

Usage:
    python scripts/verify_nearby.py
    python scripts/verify_nearby.py "附近奶茶店30分钟送到" --now-hour 14
    python scripts/verify_nearby.py "附近便宜的快餐 半小时内 人均30以内" --now-hour 12
"""

import argparse
import asyncio
import sys

# Windows 控制台默认 GBK,输出 ¥/中文会崩;强制 utf-8。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.agents.instant_constraint_parser import parse_instant_constraints
from src.retrieval.geo import DEMO_USER_LOCATION
from src.tools.merchant_search import nearby_merchant_search


async def run(query: str, location: dict, now_hour: int):
    print(f"\n用户输入: {query}")
    print(f"用户位置: {location}  |  当前时间: {now_hour:02d}:00\n")

    constraints = parse_instant_constraints(query)
    print("规则抽取约束:")
    for k, v in constraints.items():
        if k != "semantic_query":
            print(f"  - {k}: {v}")
    print()

    result = await nearby_merchant_search(
        location=location,
        semantic_query=constraints["semantic_query"],
        merchant_category=constraints["merchant_category"],
        max_delivery_minutes=constraints["max_delivery_minutes"],
        budget=constraints["budget"],
        now_hour=now_hour,
    )

    print(f"下推过滤后召回: {result['retrieved']} 家 | "
          f"精确半径过滤掉: {result['filtered_by_radius']} 家 | "
          f"最终返回: {result['total']} 家 | "
          f"耗时 {result['latency_ms']}ms\n")

    if not result["results"]:
        print("⚠️  无满足约束的门店。")
        return

    print("满足'附近 + 营业中 + 时效 + 预算'的门店(时效优先排序):")
    for i, m in enumerate(result["results"], 1):
        print(f"  {i}. {m['name']}  [{m['category']}]")
        print(f"     距离 {m['distance_km']}km | ETA {m['delivery_minutes']}分钟 | "
              f"配送费 ¥{m['delivery_fee']} | 评分 {m['rating']} | 营业 {m['open_hours']}")
        print(f"     人均 ¥{m['avg_price']} | rank={m['rank_score']} ({m['rank_reason_text']})")
    print()


def main():
    parser = argparse.ArgumentParser(description="Verify 秒送 就近检索")
    parser.add_argument("query", nargs="?", default="附近奶茶店30分钟送到")
    parser.add_argument("--lat", type=float, default=DEMO_USER_LOCATION["lat"])
    parser.add_argument("--lon", type=float, default=DEMO_USER_LOCATION["lon"])
    parser.add_argument("--now-hour", type=int, default=14,
                        help="当前小时(0-23),默认 14 便于确定性验证白天营业")
    args = parser.parse_args()

    location = {"lat": args.lat, "lon": args.lon}
    asyncio.run(run(args.query, location, args.now_hour))


if __name__ == "__main__":
    main()
