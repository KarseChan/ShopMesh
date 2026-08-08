"""Verify the instant_order intent routing (P1 slice 1b).

Drives the real graph nodes preprocess → agent_router → instant_order_agent
(skips postprocess so no Celery/RabbitMQ needed), proving:
  自然语言 → 意图路由到 instant_order_agent → 附近满足约束的门店出。

Prereqs: infra (Qdrant + Ollama) + built merchant index (see verify_nearby.py).

Usage:
    python scripts/verify_instant_order.py
    python scripts/verify_instant_order.py "附近快餐 半小时送到 人均30以内"
"""

import argparse
import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.graph.preprocessing import node_preprocess
from src.graph.specialized_agents import (
    node_agent_router,
    node_instant_order_agent,
    route_to_agent,
)


async def run(query: str):
    print(f"\n用户输入: {query}\n")
    state = {"messages": [{"role": "user", "content": query}],
             "user_id": "verify_u", "session_id": "verify_s"}

    pre = await node_preprocess(state)
    print(f"意图路由: user_goals={pre['user_goals']}  task_type={pre['intent'].get('task_type')}")
    state.update(pre)

    routed = await node_agent_router(state)
    state.update(routed)
    node_name = route_to_agent(state)
    print(f"确定性路由 → active_agent={routed.get('active_agent')}  (graph node: {node_name})\n")

    if node_name != "instant_order_agent":
        print(f"⚠️  未路由到 instant_order_agent(实际 {node_name})。")
        return

    out = await node_instant_order_agent(state)
    merchants = out["response_data"]["merchants"]
    print(f"response_type={out['response_type']}  返回门店={len(merchants)} 家\n")
    print(out["final_response"])
    print()


def main():
    parser = argparse.ArgumentParser(description="Verify instant_order 路由链路")
    parser.add_argument("query", nargs="?", default="附近奶茶店30分钟送到")
    args = parser.parse_args()
    asyncio.run(run(args.query))


if __name__ == "__main__":
    main()
