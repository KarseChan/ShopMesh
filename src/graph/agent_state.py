"""AgentState definition for the hybrid Agent graph.

Design principles:
1. Deterministic preprocessing writes: intent, entities, memory_chunks
2. ReAct Agent writes: search_results, tool_calls_log, iteration, final_response
3. Postprocessing reads: final_response, entities, user_id
4. Reducer fields use Annotated[list, add] for parallel-safe append
"""

from typing import Annotated, TypedDict

from langgraph.graph import add_messages


def _add_lists(left: list, right: list) -> list:
    """Reducer: concatenate two lists (for parallel-safe append)."""
    return left + right


class AgentState(TypedDict):
    """State for the hybrid Agent graph (preprocess → react → postprocess)."""

    # === Dialog ===
    messages: Annotated[list, add_messages]   # Dialog history
    user_id: str                               # User ID
    session_id: str                             # Session ID (for Redis keys)

    # === Session memory (L2a/L2b) ===
    session_window: list                        # L2a: recent N turns from Redis
    session_summary: str                        # L2b: LLM-compressed older turns

    # === Deterministic preprocessing output ===
    intent: dict                               # Intent classification result {user_goals, task_type, execution_hint}
    user_goals: list[str]                       # Multi-label intent goals (e.g. ["compare_products", "recommend_product"])
    entities: dict                              # Extracted entities
    memory_chunks: list                         # Recalled memory chunks
    search_plan: dict                           # Search plan from search_planner

    # === ReAct Agent dynamic decision ===
    search_results: list                        # Retrieval results
    user_profile: dict                          # User profile (inferred from memory)
    tool_calls_log: Annotated[list, _add_lists] # Tool call observation log
    iteration: int                              # ReAct loop iteration count
    max_iterations: int                         # Max iterations (default 5)
    final_response: str | None                  # Final response (termination signal)
    recommendations: list                       # Structured per-product recommendations [{product_id, text}]
    asked_fields: list                          # Fields already asked about
    used_fallback: bool                         # Whether fallback path was used

    # === Clarification state ===
    pending_clarification: dict | None          # Awaiting clarification answer {fields, question_spec, question_type, strategy, entities_snapshot}

    # === Multi-Agent routing ===
    active_agent: str                           # Which specialized agent is active (e.g. "search_recommend_agent")
    response_type: str                          # Frontend rendering hint (e.g. "recommendation_cards")
    response_data: dict                         # Structured response data for frontend (parsed from agent JSON output)

    # === Narrative streaming ===
    selected_product_ids: list                  # Product IDs selected by agent for narrative streaming
    stream_narrative: bool                      # Whether to use narrative streaming for response
