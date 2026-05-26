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
from src.memory.memory_retriever import recall, should_recall_dual
from src.memory.session_memory import get_session_memory
from src.memory.user_profile import get_global_profile
from src.observability.logger import get_logger
from src.router.intent_classifier import classify_intent

# Scenarios where null category/product_type is expected (not ambiguous)
_NO_DISAMBIGUATE_SCENARIOS = {"送礼", "礼物", "生日礼物", "节日礼物", "情人节", "圣诞节"}

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


def _detect_task_switch(current: dict, previous: dict) -> bool:
    """Detect if user switched to a different task between turns.

    Returns True if the current turn's scenario/category/product_type differs
    significantly from the previous turn, meaning task-specific context (price,
    scenario, soft_requirements) should NOT be inherited.

    Structural fields (category, product_type, brand) can still be inherited
    as search narrowing hints.
    """
    if not previous:
        return False

    prev_scenario = previous.get("scenario", "")
    cur_scenario = current.get("scenario", "")

    # Scenario changed → definite task switch (e.g. "送礼" → "面试")
    if prev_scenario and cur_scenario and prev_scenario != cur_scenario:
        logger.info("task_switch_detected", signal="scenario_changed",
                    prev=prev_scenario, cur=cur_scenario)
        return True

    prev_category = previous.get("category", "")
    cur_category = current.get("category", "")

    # Cross-category switch (e.g. "服饰" → "数码")
    if prev_category and cur_category and prev_category != cur_category:
        logger.info("task_switch_detected", signal="category_changed",
                    prev=prev_category, cur=cur_category)
        return True

    prev_pt = previous.get("product_type", "")
    cur_pt = current.get("product_type", "")

    # Different product_type with no overlap (e.g. "双肩包" → "手机")
    # But NOT if current has no product_type (just a follow-up like "再推荐女士的")
    if prev_pt and cur_pt and prev_pt != cur_pt:
        # Check if they're in the same broad clothing family
        _CLOTHING_TYPES = {"衬衫", "T恤", "Polo衫", "卫衣", "外套", "夹克", "西装",
                           "西装外套", "针织衫", "羽绒服", "裤子", "裤装", "牛仔裤",
                           "西裤", "休闲裤", "短裤", "裙子", "裙装", "半身裙",
                           "长裙", "连衣裙", "衣服", "穿搭", "正装"}
        if prev_pt in _CLOTHING_TYPES and cur_pt in _CLOTHING_TYPES:
            return False  # Same clothing family, not a switch
        logger.info("task_switch_detected", signal="product_type_changed",
                    prev=prev_pt, cur=cur_pt)
        return True

    return False


async def _run_normal_preprocessing(user_input: str, user_id: str, state: dict) -> dict:
    """Path A: normal intent + entity + memory + session (parallel)."""
    intent_task = classify_intent(user_input)
    entity_task = extract_entities(user_input)

    prev_category = state.get("entities", {}).get("category")
    do_recall, recall_reason = await should_recall_dual(
        user_input, user_id,
        current_category=None, prev_category=prev_category,
    )
    if do_recall:
        memory_task = recall(user_id, user_input)
        logger.info("recall_triggered", reason=recall_reason)
    else:
        memory_task = _empty_list()

    # Load L2a/L2b from Redis (parallel with intent/entity/memory)
    session_id = state.get("session_id", user_id)
    session_mem = get_session_memory(session_id)
    window_task = session_mem.get_window()      # L2a sliding window
    summary_task = session_mem.get_summary()    # L2b compressed summary

    # Load L3 user profile (sync → async wrapper)
    profile_task = asyncio.to_thread(get_global_profile, user_id)

    intent_result, entities, memories, window, summary, user_profile = await asyncio.gather(
        intent_task, entity_task, memory_task, window_task, summary_task, profile_task
    )

    intent, confidence, source = intent_result

    # Context carry-forward: inherit missing fields from previous turn's entities
    prev_entities = state.get("entities", {})
    task_switched = _detect_task_switch(entities, prev_entities)

    if prev_entities:
        if task_switched:
            # Task switch: clear ALL previous context, only use current turn's entities.
            # Do NOT inherit category/product_type/brand — they belong to the old task.
            _ALL_CONTEXT_FIELDS = ("category", "product_type", "brand",
                                   "scenario", "price_min", "price_max",
                                   "soft_requirements", "hard_constraints")
            cleared = [f for f in _ALL_CONTEXT_FIELDS if prev_entities.get(f)]
            for field in _ALL_CONTEXT_FIELDS:
                if not entities.get(field) and prev_entities.get(field):
                    entities[field] = None if not isinstance(prev_entities.get(field), list) else []
            if cleared:
                logger.info("context_inheritance_cleared", reason="task_switch",
                            cleared_fields=cleared)
        else:
            # Same task continuation: inherit all missing fields
            for field in ("category", "product_type", "brand", "scenario", "price_min", "price_max"):
                if not entities.get(field) and prev_entities.get(field):
                    entities[field] = prev_entities[field]
                    logger.info("context_inherited", field=field,
                                value=str(prev_entities[field])[:50])
            # Merge hard_constraints from previous turn (e.g. price range) into current
            prev_hc = prev_entities.get("hard_constraints", {})
            cur_hc = entities.get("hard_constraints", {})
            if prev_hc:
                merged = {**prev_hc, **cur_hc}  # current overrides previous
                entities["hard_constraints"] = merged
                if merged != cur_hc:
                    logger.info("context_inherited_hard_constraints",
                                merged_keys=list(merged.keys()))

    if entities.get("soft_requirements"):
        entities["soft_requirements"] = normalize_soft_requirements(entities["soft_requirements"])

    entities = validate_entities(entities)

    # Auto-inject gift_context soft_requirement when scenario is gift-related
    # but LLM didn't extract any soft_requirements (LLM extraction is unstable)
    scenario = entities.get("scenario", "")
    if scenario in _NO_DISAMBIGUATE_SCENARIOS and not entities.get("soft_requirements"):
        entities["soft_requirements"] = normalize_soft_requirements([{
            "raw_text": f"{scenario}场景",
            "canonical": scenario,
            "type": "gift_context",
            "importance": 0.8,
        }])
        logger.info("auto_injected_soft_requirement", scenario=scenario, type="gift_context")

    logger.info("preprocess_done",
                intent=intent, confidence=confidence, source=source,
                entity_category=entities.get("category"),
                memory_count=len(memories),
                soft_req_count=len(entities.get("soft_requirements", [])))

    # Skip disambiguation for scenarios where null category/product_type is expected,
    # or for comparison queries (multiple brands are expected, disambiguation is irrelevant)
    user_goals = intent.get("user_goals", []) if isinstance(intent, dict) else []
    if not user_goals:
        single = intent.get("user_goal", "") if isinstance(intent, dict) else ""
        user_goals = [single] if single else []
    is_compare = "compare_products" in user_goals

    if entities.get("ambiguous") and scenario not in _NO_DISAMBIGUATE_SCENARIOS and not is_compare:
        entities["_raw_query"] = user_input
        disambig_result = await disambiguate(entities)
        entities = disambig_result["entities"]
        if not disambig_result["resolved"]:
            logger.info("disambiguation_pending", question=disambig_result.get("question"))
    elif entities.get("ambiguous") and (scenario in _NO_DISAMBIGUATE_SCENARIOS or is_compare):
        # Clear ambiguous flag — disambiguation is not needed for these cases
        entities["ambiguous"] = False
        entities["ambiguous_fields"] = []
        logger.info("disambiguation_skipped",
                     reason=f"scenario '{scenario}'" if scenario in _NO_DISAMBIGUATE_SCENARIOS else f"intent '{user_goals}'")

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

    # Extract user_goals as top-level field for agent_router
    user_goals = intent.get("user_goals", []) if isinstance(intent, dict) else []
    if not user_goals:
        single = intent.get("user_goal", "") if isinstance(intent, dict) else ""
        user_goals = [single] if single else ["recommend_product"]

    return {
        "intent": intent,
        "user_goals": user_goals,
        "entities": entities,
        "memory_chunks": memories if isinstance(memories, list) else [],
        "search_plan": search_plan,
        "session_window": window,
        "session_summary": summary,
        "user_profile": user_profile,
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

            # Preserve user_goals from previous intent
            prev_goals = intent.get("user_goals", []) if isinstance(intent, dict) else []
            if not prev_goals:
                single = intent.get("user_goal", "") if isinstance(intent, dict) else ""
                prev_goals = [single] if single else ["recommend_product"]

            return {
                "intent": intent,
                "user_goals": prev_goals,
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
        prev_intent = state.get("intent", {})
        prev_goals = prev_intent.get("user_goals", []) if isinstance(prev_intent, dict) else []
        if not prev_goals:
            single = prev_intent.get("user_goal", "") if isinstance(prev_intent, dict) else ""
            prev_goals = [single] if single else ["recommend_product"]

        return {
            "intent": prev_intent,
            "user_goals": prev_goals,
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
