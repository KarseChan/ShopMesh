"""Deterministic Postprocessing — session memory + two-level preference extraction.

Runs after Agent finishes.

Main path (sync, < 5ms):
  - L2a: add_turn to Redis sliding window
  - L1 rule filter: noise → skip, strong signal → write immediately

Background path (Celery tasks):
  - L2b: trim evicted turns → LLM compression
  - L2c: batch LLM preference classification (every 3 turns, with SETNX lock)
  - Conversation persistence, vector memory writes, profile updates
"""

import asyncio
import json
import re

from src.memory.session_memory import get_session_memory
from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("postprocessing")

# === Level 1: Noise patterns (skip entirely, zero latency) ===
_NOISE_PATTERNS = [
    r"^(好的|嗯|哦|行|可以|谢谢|谢了|知道了|好吧|没问题)$",
    r"^(第[一二三]个|这个|那个|左边|右边|上面|下面)$",
    r"^帮我(看看|瞧瞧|搜搜|查查|找找)",
    r"^(还有|还有没有|再看看|换一个)",
]

# === Level 1: Strong signal patterns (write immediately, skip LLM) ===
_STRONG_SIGNAL_PATTERNS = [
    r"我一直", r"我一直都", r"我是\w+皮",
    r"我平时", r"我习惯", r"我喜欢\w+风格",
    r"我偏好", r"我的肤质", r"我属于",
    r"我常用", r"我经常买", r"我不喜欢\w+牌",
    r"不喜欢\w+", r"不要\w+的", r"不想\w+的",
    r"排除\w+", r"不要\w+品牌",
    r"预算.{0,5}\d+", r"不要超过\d+", r"\d+以内",
    r"我的预算", r"我预算",
]

# === Level 2: LLM batch classification prompt ===
_PREFERENCE_CLASSIFY_PROMPT = """分析以下对话，提取用户的长期偏好。

长期偏好：身份特征、稳定习惯、持久偏好（肤质、常买品牌、价格敏感度、风格偏好）
临时需求：一次性购买请求、当下场景需求、纯操作指令

对话记录：
{conversation}

输出 JSON: {{
  "preferences": [
    {{"text": "用户原文摘录", "is_long_term": bool, "preference_type": str, "confidence": float}}
  ]
}}
preference_type: "skin_type" | "brand_preference" | "price_sensitivity" | "style_preference" | "temporary" | "other"

如果对话中没有明显的长期偏好，返回空数组。"""


def _get_user_input(state: dict) -> str:
    messages = state.get("messages", [])
    if not messages:
        return ""
    msg = messages[-1]
    if isinstance(msg, dict):
        return msg.get("content", "")
    return getattr(msg, "content", "")


def _get_final_response(state: dict) -> str:
    return state.get("final_response", "")


def _is_noise(text: str) -> bool:
    """Level 1: obvious filler/operational text, skip entirely."""
    text = text.strip()
    return any(re.match(p, text) for p in _NOISE_PATTERNS)


def _has_strong_signal(text: str) -> bool:
    """Level 1: explicit preference signals, write immediately without LLM."""
    return any(re.search(p, text) for p in _STRONG_SIGNAL_PATTERNS)


async def _batch_classify_preferences(session_id: str, user_id: str, category: str) -> None:
    """Level 2: batch LLM preference classification (background task).

    Reads recent 3 turns from Redis, sends to LLM for preference extraction.
    Results written to L2c vector memory AND L3 user profile.
    """
    from src.db.redis_client import get_redis
    redis = get_redis()
    lock_key = f"lock:batch_memory:{session_id}"

    try:
        session_mem = get_session_memory(session_id)
        window = await session_mem.get_window()

        if len(window) < 6:  # at least 3 turns (6 messages)
            return

        # Take the most recent 3 turns
        recent = window[-6:]
        conversation = "\n".join(f"{m['role']}: {m['content']}" for m in recent)

        llm = get_llm()
        result = await llm.chat_json([
            {"role": "system", "content": "你是偏好分析助手。只输出 JSON。"},
            {"role": "user", "content": _PREFERENCE_CLASSIFY_PROMPT.format(
                conversation=conversation,
            )},
        ])

        # Write confirmed long-term preferences to L2c + L3
        for pref in result.get("preferences", []):
            if pref.get("is_long_term") and pref.get("confidence", 0) > 0.7:
                # L2c: vector memory (with contradiction detection)
                await write_chunk_with_contradiction_awareness(
                    user_id=user_id,
                    user_input=pref["text"],
                    assistant_output="[批量偏好分析提取]",
                    entities={},
                    intent="preference",
                    category=category,
                )
                # L3: update user profile (brand/price/style)
                pref_type = pref.get("preference_type", "")
                if pref_type in ("brand_preference", "price_sensitivity", "skin_type", "style_preference"):
                    await update_profile_from_preference(
                        user_id=user_id,
                        category=category,
                        preference_type=pref_type,
                        text=pref["text"],
                        confidence=pref.get("confidence", 0),
                    )
                logger.info("batch_preference_saved",
                           text=pref["text"][:50],
                           pref_type=pref_type)

    except Exception as e:
        logger.error("batch_classify_error", error=str(e))
    finally:
        # Release lock
        await redis.delete(lock_key)
        logger.info("batch_lock_released", session_id=session_id)


async def node_postprocess(state: dict) -> dict:
    """Deterministic postprocessing: session memory + two-level preference extraction.

    Main path (< 5ms):
      1. L2a add_turn (sync, Redis RPUSH)
      2. Level 1 rule filter (local, zero latency)

    Background path (fire-and-forget):
      1. L2b trim (LLM compression)
      2. L2c write_chunk (strong signal) or batch classify (every 3 turns)

    Returns empty dict (no state changes needed).
    """
    user_input = _get_user_input(state)
    response = _get_final_response(state)
    entities = state.get("entities", {})
    intent_raw = state.get("intent", {})
    # Support multi-label user_goals (list) or legacy user_goal (string)
    if isinstance(intent_raw, dict):
        goals = intent_raw.get("user_goals", [])
        if not goals:
            single = intent_raw.get("user_goal", "")
            goals = [single] if single else []
        intent = ",".join(goals)
    else:
        intent = str(intent_raw)
    user_id = state.get("user_id", "default_user")
    session_id = state.get("session_id", user_id)

    if not user_input or not response:
        return {}

    # Output sanitization: mask any PII patterns in the response text
    from src.security.data_guard import mask_phone, mask_card_number, mask_id_number
    response = mask_phone(response)
    response = mask_card_number(response)
    response = mask_id_number(response)

    from src.tasks.memory_tasks import (
        trim_session,
        write_vector_memory,
        batch_classify_preferences,
        update_profile_preference,
        save_conversation_message,
    )

    # ---- L2a: Write sliding window (sync, Redis RPUSH < 1ms) ----
    session_mem = get_session_memory(session_id)
    await session_mem.add_turn(user_input, response)

    # ---- Persistent conversation storage → Celery ----
    save_conversation_message.delay(user_id, session_id, "user", user_input)
    save_conversation_message.delay(user_id, session_id, "assistant", response)

    # ---- L2b: Trim evicted turns to summary → Celery ----
    from src.auth.context import get_tenant_id
    trim_session.delay(session_id, tenant_id=get_tenant_id())

    # ---- L2c: Two-level preference extraction ----
    category = entities.get("category", "") or ""
    if _has_strong_signal(user_input):
        # Level 1: strong signal → write immediately via Celery
        write_vector_memory.delay(
            user_id=user_id,
            user_input=user_input,
            assistant_output=response,
            entities=entities,
            intent=intent,
            category=category,
        )
        # Also update L3 profile for brand/price strong signals
        if any(kw in user_input for kw in ["品牌", "喜欢", "不喜欢", "不要", "排除", "不想"]):
            update_profile_preference.delay(
                user_id=user_id,
                category=category or "通用",
                preference_type="brand_preference",
                text=user_input,
                confidence=0.9,
            )
        if any(kw in user_input for kw in ["预算", "便宜", "性价比", "不要太贵"]):
            update_profile_preference.delay(
                user_id=user_id,
                category=category or "通用",
                preference_type="price_sensitivity",
                text=user_input,
                confidence=0.9,
            )
        logger.info("memory_saved", user_id=user_id, reason="strong_signal")

    elif _is_noise(user_input):
        # Level 1: noise → skip entirely
        logger.info("memory_skipped", user_id=user_id, reason="noise")

    else:
        # Middle ground: defer to batch LLM classification
        from src.db.redis_client import get_redis
        redis = get_redis()
        counter_key = f"session:{session_id}:turn_count"
        count = await redis.incr(counter_key)
        await redis.expire(counter_key, 3600)

        if count % 3 == 0:
            # SETNX lock: only one batch task per session at a time
            lock_key = f"lock:batch_memory:{session_id}"
            acquired = await redis.set(lock_key, "1", nx=True, ex=10)
            if acquired:
                batch_classify_preferences.delay(session_id, user_id, category)
                logger.info("batch_triggered", turn_count=count)
            else:
                logger.info("batch_skipped", reason="lock_held", turn_count=count)
        else:
            logger.info("memory_deferred", user_id=user_id, turn_count=count)

    return {}
