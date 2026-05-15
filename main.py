"""ShoppingAgent CLI — minimal agent loop.

Usage:
    python main.py "帮我找一杯奶茶"
    python main.py  (interactive mode)
"""

import asyncio
import sys

from src.graph.shopping_graph import run_shopping, run_shopping_stream


async def run_once(user_input: str):
    result = await run_shopping(user_input)
    print(result.get("explanation", ""))


async def run_once_stream(user_input: str):
    async for event in run_shopping_stream(user_input):
        etype = event["event"]
        data = event["data"]
        if etype == "intent":
            print(f"[意图] {data.get('intent', '')}")
        elif etype == "entities":
            print(f"[实体] {data.get('entities', {})}")
        elif etype == "clarification":
            print(f"[追问] {data.get('explanation', '')}")
        elif etype == "explanation":
            print(f"\n{data.get('text', '')}")
        elif etype == "done":
            print(f"\n[完成] 耗时 {data.get('latency_ms', 0):.0f}ms")


async def interactive():
    print("ShoppingAgent — 输入需求开始购物，输入 quit 退出\n")
    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break
        result = await run_shopping(user_input)
        print(f"\nAgent: {result.get('explanation', '')}")


def main():
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
        if "--stream" in text:
            text = text.replace("--stream", "").strip()
            asyncio.run(run_once_stream(text))
        else:
            asyncio.run(run_once(text))
    else:
        asyncio.run(interactive())


if __name__ == "__main__":
    main()
