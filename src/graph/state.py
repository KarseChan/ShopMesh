"""ShoppingState definition for the shopping agent graph."""

from typing import Annotated, TypedDict

from langgraph.graph import add_messages


class ShoppingState(TypedDict):
    """Shared state for all agents in the shopping graph.

    Fields are split into three categories:
    - Reducer fields: parallel nodes can safely append (Annotated[list, add_messages])
    - Exclusive fields: each agent writes its own field, no reducer needed
    - Read-write fields: shared counters / flags
    """

    # Reducer fields (parallel-safe append)
    messages: Annotated[list, add_messages]

    # Exclusive fields (each agent writes its own)
    intent: str
    entities: dict
    search_results: list
    explanation: str

    # Read-write fields
    clarification_count: int
