"""ShoppingState definition for the shopping agent graph.

Design principles:
1. State = working memory, only data that affects graph routing decisions
2. Execution logs (telemetry) → logger.info(), NOT in State
3. Dialog history → RemoveMessage pruning at end of each turn
4. Parallel-append fields → Annotated[list, add_messages] or Annotated[list, add]
5. Exclusive-write fields → plain types (each agent writes its own field)
"""

from typing import Annotated, TypedDict

from langgraph.graph import add_messages


def _add_lists(left: list, right: list) -> list:
    """Reducer: concatenate two lists (for parallel-safe append)."""
    return left + right


class ShoppingState(TypedDict):
    """Shared state for all agents in the shopping graph.

    Fields are split into three categories:
    - Reducer fields: parallel nodes can safely append
    - Exclusive fields: each agent writes its own field, no reducer needed
    - Read-write fields: shared counters / flags
    """

    # === Reducer fields (parallel-safe append) ===
    messages: Annotated[list, add_messages]  # Dialog history, pruned with RemoveMessage
    tool_calls: Annotated[list, _add_lists]  # Tool call records (this turn)
    errors: Annotated[list, _add_lists]      # Error records, parallel append

    # === Exclusive fields (each agent writes its own) ===
    intent: dict                             # Intent Classifier {user_goal, task_type, execution_hint}
    entities: dict                           # Entity Extractor
    memory_chunks: list                      # Memory Retriever (L2c)
    search_plan: dict                        # Search Planner (检索策略)
    search_results: list                     # Hybrid Retriever
    promotion_info: dict                     # Promotion Calculator
    ranked_results: list                     # Ranker
    explanation: str                         # Explainer

    # === Read-write fields ===
    clarification_count: int                 # Clarification Engine increments
    asked_fields: list                       # Fields already asked about (clarification dedup)
    clarification_options: list              # Clickable options for current clarification question
    clarification_questions: list            # Batch of clarification questions
    pending_clarification: dict | None       # Awaiting clarification answer {fields, question_spec, question_type, strategy, entities_snapshot}

    # === HITL order fields ===
    order_info: dict | None                  # Prepared order details (for interrupt)
    resume_confirmed: bool | None            # User confirmation from Command(resume=...)
