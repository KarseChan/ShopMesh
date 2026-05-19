"""Deterministic Preprocessing — fixed execution before ReAct Agent.

Runs intent classification, entity extraction, and memory recall in parallel.
These are low-risk, structured tasks that should always execute deterministically.
"""

import asyncio

from src.agents.clarification_parser import parse_clarification_answer
from src.agents.clarification_router import route_clarification
from src.agents.disambiguator import disambiguate
from src.agents.entity_extractor import extract_entities
from src.agents.entity_validator import validate_entities
from src.agents.normalizer import normalize_soft_requirements
from src.agents.search_planner import plan_search
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


async def _run_normal_preprocessing(user_input: str, user_id: str, state: dict) -> dict:
    """Path A: normal intent + entity + memory extraction (parallel)."""
    intent_task = classify_intent(user_input)
    entity_task = extract_entities(user_input)

    prev_category = state.get("entities", {}).get("category")
    if should_recall(user_input, current_category=None, prev_category=prev_category):
        memory_task = recall(user_id, user_input)
    else:
        memory_task = _empty_list()

    intent_result, entities, memories = await asyncio.gather(
        intent_task, entity_task, memory_task
    )

    intent, confidence, source = intent_result

    if entities.get("soft_requirements"):
        entities["soft_requirements"] = normalize_soft_requirements(entities["soft_requirements"])

    entities = validate_entities(entities)

    logger.info("preprocess_done",
                intent=intent, confidence=confidence, source=source,
                entity_category=entities.get("category"),
                memory_count=len(memories),
                soft_req_count=len(entities.get("soft_requirements", [])))

    if entities.get("ambiguous"):
        entities["_raw_query"] = user_input
        disambig_result = await disambiguate(entities)
        entities = disambig_result["entities"]
        if not disambig_result["resolved"]:
            logger.info("disambiguation_pending", question=disambig_result.get("question"))

    missing_fields = entities.get("missing_critical_fields", [])
    if missing_fields:
        logger.info("search_plan_skip", reason="missing_critical_fields", fields=missing_fields)
        search_plan = {
            "search_mode": "deferred",
            "reason": f"缺失关键字段 {missing_fields}，等待澄清后再规划",
            "primary_constraints": {},
            "target_product_types": [],
            "search_requests": [],
        }
    else:
        search_plan = await plan_search(entities, user_input)

    return {
        "intent": intent,
        "entities": entities,
        "memory_chunks": memories if isinstance(memories, list) else [],
        "search_plan": search_plan,
    }


async def node_preprocess(state: dict) -> dict:
    """Deterministic preprocessing: intent + entity + memory (parallel).

    Handles three paths:
    A. Normal: classify_intent + extract_entities + recall (parallel)
    B. Clarification answer: parse user reply and merge into previous entities
    C. Task switch: user changed task, fall back to Path A (with optional context carry-over)

    Returns dict to merge into AgentState:
        intent, entities, memory_chunks, search_plan
    """
    user_input = _get_user_input(state)
    user_id = state.get("user_id", "default_user")

    # === Path B/C: Pending clarification exists ===
    pending = state.get("pending_clarification")
    if pending:
        previous_entities = pending.get("entities_snapshot", {})
        pending_fields = pending.get("fields", [])
        question_type = pending.get("question_type", "")

        logger.info("clarification_pending_detected",
                     pending_fields=pending_fields,
                     question_type=question_type,
                     user_input=user_input[:50])

        # Route: is this a clarification answer, task switch, or unclear?
        routing = await route_clarification(
            user_input, pending_fields, previous_entities, question_type)

        route = routing["route"]

        # --- Path C: Task switch (full) ---
        if route == "task_switch_full":
            logger.info("clarification_task_switch",
                        switch_type="full",
                        confidence=routing["confidence"],
                        reason=routing["reason"],
                        new_input=routing.get("new_input", ""))
            # Clear pending, run normal Path A with the full user input
            state["pending_clarification"] = None
            return await _run_normal_preprocessing(user_input, user_id, state)

        # --- Path C: Task switch (partial) — keep scenario/soft_requirements ---
        if route == "task_switch_partial":
            logger.info("clarification_task_switch",
                        switch_type="partial",
                        confidence=routing["confidence"],
                        reason=routing["reason"],
                        parsed_fields=routing.get("parsed_fields", {}))
            # Clear pending, run normal Path A
            state["pending_clarification"] = None
            result = await _run_normal_preprocessing(user_input, user_id, state)
            # Inject preserved context from previous turn
            entities = result["entities"]
            for field in ("scenario", "soft_requirements"):
                prev_val = previous_entities.get(field)
                if prev_val and not entities.get(field):
                    entities[field] = prev_val
                    logger.info("context_injected_from_previous",
                                field=field, value=str(prev_val)[:50])
            # If router parsed a product_type, inject it
            parsed = routing.get("parsed_fields", {})
            if parsed.get("product_type") and not entities.get("product_type"):
                entities["product_type"] = parsed["product_type"]
            # Re-validate after injection
            entities = validate_entities(entities)
            result["entities"] = entities
            result["pending_clarification"] = None
            return result

        # --- Path B: Clarification answer ---
        if route == "clarification_answer":
            parsed_fields = routing.get("parsed_fields", {})
            # Merge parsed fields into previous entities (no second parser call)
            entities = {**previous_entities}
            entities.update(parsed_fields)

            # Remove resolved fields from missing_critical_fields
            resolved_fields = [f for f in pending_fields if parsed_fields.get(f) is not None]
            if resolved_fields:
                missing = entities.get("missing_critical_fields", [])
                entities["missing_critical_fields"] = [f for f in missing if f not in resolved_fields]
                if not entities["missing_critical_fields"]:
                    entities["ambiguous"] = False
                    entities["ambiguous_fields"] = []

            # Keep previous intent
            intent = state.get("intent", {})

            # Log merge result
            carried_fields = sorted(k for k in previous_entities
                                    if k not in pending_fields
                                    and previous_entities.get(k) is not None
                                    and previous_entities.get(k) != []
                                    and previous_entities.get(k) != {})
            logger.info("context_merge_done",
                         source="pending_clarification.entities_snapshot",
                         current_entities=parsed_fields,
                         merged_entities={k: v for k, v in entities.items()
                                          if v is not None and v != [] and v != {}
                                          and not k.startswith("_")},
                         carried_fields=carried_fields,
                         gender=entities.get("gender"),
                         product_type=entities.get("product_type"),
                         scenario=entities.get("scenario"),
                         soft_req_count=len(entities.get("soft_requirements", [])),
                         remaining_missing=entities.get("missing_critical_fields", []))

            # Search planning
            missing_fields = entities.get("missing_critical_fields", [])
            if missing_fields:
                logger.info("search_plan_skip", reason="still_missing", fields=missing_fields)
                search_plan = {
                    "search_mode": "deferred",
                    "reason": f"仍有缺失字段 {missing_fields}",
                    "primary_constraints": {},
                    "target_product_types": [],
                    "search_requests": [],
                }
            else:
                search_plan = await plan_search(entities, user_input)

            # Log and clear pending
            filled_fields = [f for f in pending_fields if parsed_fields.get(f) is not None]
            reason = "all_required_fields_filled" if not missing_fields else "partial_fields_filled"
            logger.info("pending_clarification_cleared",
                         reason=reason,
                         filled_fields=filled_fields,
                         remaining_missing=missing_fields)

            return {
                "intent": intent,
                "entities": entities,
                "memory_chunks": [],
                "search_plan": search_plan,
                "pending_clarification": None,
            }

        # --- Unclear: re-ask or assume ---
        logger.info("clarification_unclear",
                     confidence=routing["confidence"],
                     reason=routing["reason"])
        # Clear pending and let Agent decide (assume strategy)
        return {
            "intent": state.get("intent", {}),
            "entities": previous_entities,
            "memory_chunks": [],
            "search_plan": {
                "search_mode": "deferred",
                "reason": "澄清回答不明确，等待 Agent 决策",
                "primary_constraints": {},
                "target_product_types": [],
                "search_requests": [],
            },
            "pending_clarification": None,
        }

    # === Path A: Normal preprocessing ===
    return await _run_normal_preprocessing(user_input, user_id, state)


async def _empty_list() -> list:
    return []
