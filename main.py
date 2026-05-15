"""ShoppingAgent CLI — minimal agent loop.

Usage:
    python main.py "帮我找一杯奶茶"
    python main.py  (interactive mode)
"""

import asyncio
import sys

from src.graph.shopping_graph import build_shopping_graph


async def run_once(user_input: str):
    app = build_shopping_graph()
    result = await app.ainvoke({
        "user_input": user_input,
        "category": None,
        "keyword": None,
        "max_price": None,
        "results": [],
        "answer": "",
    })
    print(result["answer"])


async def interactive():
    app = build_shopping_graph()
    print("ShoppingAgent — 输入需求开始购物，输入 quit 退出\n")
    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            break
        result = await app.ainvoke({
            "user_input": user_input,
            "category": None,
            "keyword": None,
            "max_price": None,
            "results": [],
            "answer": "",
        })
        print(f"\nAgent: {result['answer']}")


def main():
    if len(sys.argv) > 1:
        asyncio.run(run_once(" ".join(sys.argv[1:])))
    else:
        asyncio.run(interactive())


if __name__ == "__main__":
    main()
