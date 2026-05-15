"""Minimal shopping graph: user input -> LLM parse -> search -> rank -> output."""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from src.models.llm_client import get_llm
from src.tools.search_tool import search_products


# --- State ---
class MiniState(TypedDict):
    user_input: str
    category: str | None
    keyword: str | None
    max_price: float | None
    results: list[dict]
    answer: str


# --- Nodes ---


async def parse_intent(state: MiniState) -> dict:
    """Use LLM to extract structured search params from user input."""
    llm = get_llm()
    user_input = state["user_input"]
    messages = [
        {
            "role": "system",
            "content": (
                "你是商品搜索意图解析器。从用户输入中提取搜索条件，返回 JSON：\n"
                '{"category": "品类或null", "keyword": "关键词或null", "max_price": 数字或null}\n'
                "品类只限：护肤、奶茶、数码、服饰、食品、家居、母婴、运动\n"
                "只输出 JSON，不要其他文字。"
            ),
        },
        {"role": "user", "content": user_input},
    ]
    try:
        parsed = await llm.chat_json(messages)
        return {
            "category": parsed.get("category"),
            "keyword": parsed.get("keyword"),
            "max_price": parsed.get("max_price"),
        }
    except Exception:
        # Fallback: use raw input as keyword
        return {"category": None, "keyword": user_input, "max_price": None}


def search(state: MiniState) -> dict:
    """Search products from mock data."""
    results = search_products(
        category=state.get("category"),
        keyword=state.get("keyword"),
        max_price=state.get("max_price"),
        limit=3,
    )
    return {"results": results}


def generate_answer(state: MiniState) -> dict:
    """Format search results into a readable answer."""
    results = state["results"]
    if not results:
        return {"answer": "抱歉，没有找到符合条件的商品。"}

    lines = ["为你找到以下推荐：\n"]
    for i, p in enumerate(results, 1):
        lines.append(f"{i}. {p['name']} — ¥{p['price']}")
        lines.append(f"   平台: {p['platform_id']}  库存: {p['stock']}")
        if p.get("features"):
            lines.append(f"   特点: {', '.join(p['features'])}")
        lines.append("")

    return {"answer": "\n".join(lines)}


# --- Graph ---
def build_shopping_graph():
    graph = StateGraph(MiniState)

    graph.add_node("parse_intent", parse_intent)
    graph.add_node("search", search)
    graph.add_node("generate_answer", generate_answer)

    graph.set_entry_point("parse_intent")
    graph.add_edge("parse_intent", "search")
    graph.add_edge("search", "generate_answer")
    graph.add_edge("generate_answer", END)

    return graph.compile()
