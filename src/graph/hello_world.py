"""Minimal LangGraph example — single-node graph.

Run: python -m src.graph.hello_world
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph


# 1. Define State
class HelloState(TypedDict):
    greeting: str
    count: int


# 2. Define Nodes
def greet(state: HelloState) -> dict:
    name = state["greeting"]
    count = state["count"] + 1
    return {"greeting": f"Hello, {name}!", "count": count}


def farewell(state: HelloState) -> dict:
    return {"greeting": state["greeting"] + " Goodbye!", "count": state["count"]}


# 3. Build Graph
def build_hello_graph() -> StateGraph:
    graph = StateGraph(HelloState)

    graph.add_node("greet", greet)
    graph.add_node("farewell", farewell)

    graph.set_entry_point("greet")
    graph.add_edge("greet", "farewell")
    graph.add_edge("farewell", END)

    return graph.compile()


# 4. Run
if __name__ == "__main__":
    app = build_hello_graph()
    result = app.invoke({"greeting": "ShoppingAgent", "count": 0})
    print(f"Result: {result}")
    assert result["greeting"] == "Hello, ShoppingAgent! Goodbye!"
    assert result["count"] == 1
    print("LangGraph Hello World — OK")
