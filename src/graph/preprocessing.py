"""Deterministic Preprocessing — fixed execution before ReAct Agent.

Runs intent classification, entity extraction, and memory recall in parallel.
These are low-risk, structured tasks that should always execute deterministically.
"""

import asyncio

from src.agents.disambiguator import disambiguate
from src.agents.entity_extractor import extract_entities
from src.memory.memory_retriever import recall, should_recall
from src.observability.logger import get_logger
from src.router.intent_classifier import classify_intent

logger = get_logger("preprocessing")


def _get_user_input(state: dict) -> str:
    """Extract user input text from the last message."""
    messages = state.get("messages", [])
    if not messages:
        return ""
    msg = messages[-1]
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


async def node_preprocess(state: dict) -> dict:
    """Deterministic preprocessing: intent + entity + memory (parallel).

    Runs three tasks concurrently via asyncio.gather:
    1. classify_intent — Semantic Router → LLM fallback
    2. extract_entities — LLM-based structured extraction
    3. recall — vector memory retrieval (if triggered)

    Then resolves ambiguous entities if needed.

    Returns dict to merge into AgentState:
        intent, entities, memory_chunks
    """
    user_input = _get_user_input(state)
    user_id = state.get("user_id", "default_user")

    # Build parallel tasks
    intent_task = classify_intent(user_input)
    entity_task = extract_entities(user_input)

    # Memory recall is conditional — only if reference triggers or cross-category jump
    prev_category = state.get("entities", {}).get("category")
    if should_recall(user_input, current_category=None, prev_category=prev_category):
        memory_task = recall(user_id, user_input)
    else:
        memory_task = _empty_list()

    # Execute in parallel
    intent_result, entities, memories = await asyncio.gather(
        intent_task, entity_task, memory_task
    )

    intent, confidence, source = intent_result
    logger.info("preprocess_done",
                intent=intent, confidence=confidence, source=source,
                entity_category=entities.get("category"),
                memory_count=len(memories))

    # Disambiguate if needed
    if entities.get("ambiguous"):
        entities["_raw_query"] = user_input
        disambig_result = await disambiguate(entities)
        entities = disambig_result["entities"]
        if not disambig_result["resolved"]:
            logger.info("disambiguation_pending", question=disambig_result.get("question"))

    return {
        "intent": intent,
        "entities": entities,
        "memory_chunks": memories if isinstance(memories, list) else [],
    }


async def _empty_list() -> list:
    return []
