"""Deterministic Postprocessing — preference extraction + memory update.

Runs after Agent finishes. Extracts long-term preferences from dialog
and writes to vector memory if appropriate.

Key design: should_save_memory distinguishes long-term preference from
temporary need to avoid polluting user profile.
"""

import asyncio
import re

from src.memory.memory_retriever import write_chunk
from src.memory.session_memory import get_session_memory
from src.observability.logger import get_logger

logger = get_logger("postprocessing")

# Long-term preference indicators (user attributes / stable habits)
_LONG_TERM_PATTERNS = [
    r"我一直",
    r"我一直都",
    r"我是\w+皮",         # 我是油皮/干皮/敏感肌
    r"我平时",
    r"我习惯",
    r"我喜欢\w+风格",     # 我喜欢简约风格
    r"我偏好",
    r"我的肤质",
    r"我属于",
    r"我常用",
    r"我经常买",
]

# Temporary need indicators (one-time / situational)
_TEMPORARY_PATTERNS = [
    r"帮我找",
    r"帮我推荐",
    r"帮我选",
    r"今天",
    r"这次",
    r"现在",
    r"马上",
    r"急需",
    r"临时",
    r"送[人朋友]",        # 送人/送朋友
]


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


def _is_long_term_preference(text: str) -> bool:
    """Check if text contains long-term preference signals."""
    for pattern in _LONG_TERM_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def _is_temporary_need(text: str) -> bool:
    """Check if text is a temporary/situational need."""
    for pattern in _TEMPORARY_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def should_save_memory(user_input: str, entities: dict) -> bool:
    """Decide whether this interaction represents a long-term preference.

    Rules:
    - Long-term patterns (我一直/我是油皮) → save
    - Temporary patterns (帮我找/送人) → skip
    - User attributes (skin_type, concerns) → save
    - Default → skip (conservative: avoid noise)
    """
    # Explicit long-term signal
    if _is_long_term_preference(user_input):
        return True

    # Explicit temporary signal
    if _is_temporary_need(user_input):
        return False

    # User attributes from entity extraction (skin_type, concerns are stable)
    if entities.get("skin_type") or entities.get("concerns"):
        return True

    return False


async def node_postprocess(state: dict) -> dict:
    """Deterministic postprocessing: session memory + preference extraction.

    Runs after Agent produces final_response.
    Main path: L2a add_turn (sync, Redis < 1ms).
    Background: L2b trim (async, involves LLM compression 1~3s).
    Background: L2c write_chunk (async, if preference detected).

    Returns empty dict (no state changes needed).
    """
    user_input = _get_user_input(state)
    response = _get_final_response(state)
    entities = state.get("entities", {})
    intent_raw = state.get("intent", {})
    intent = intent_raw.get("user_goal", "") if isinstance(intent_raw, dict) else str(intent_raw)
    user_id = state.get("user_id", "default_user")
    session_id = state.get("session_id", user_id)

    if not user_input or not response:
        return {}

    # ---- L2a: Write sliding window (sync, Redis RPUSH < 1ms) ----
    session_mem = get_session_memory(session_id)
    await session_mem.add_turn(user_input, response)

    # ---- L2b: Trim evicted turns to summary (async, LLM 1~3s) ----
    asyncio.create_task(session_mem.trim())

    # ---- L2c: Write to vector memory if preference detected (async) ----
    if should_save_memory(user_input, entities):
        asyncio.create_task(write_chunk(
            user_id=user_id,
            user_input=user_input,
            assistant_output=response,
            entities=entities,
            intent=intent,
            category=entities.get("category"),
        ))
        logger.info("memory_saved", user_id=user_id,
                     category=entities.get("category"))
    else:
        logger.info("memory_skipped", user_id=user_id,
                     reason="temporary_need")

    return {}
