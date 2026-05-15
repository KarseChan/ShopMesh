"""L2 Session Memory — sliding window + LLM summary via Redis.

L2a: Recent N turns stored as a Redis List.
L2b: Older turns compressed into a summary string.

Storage keys:
    session:{session_id}:messages  — List of JSON-encoded messages (L2a)
    session:{session_id}:summary   — Compressed summary string (L2b)
"""

import json

from src.db.redis_client import get_redis
from src.memory.compressor import compress
from src.observability.logger import get_logger
from src.config import config

logger = get_logger("session_memory")

WINDOW_SIZE: int = config.get("memory", {}).get("sliding_window_rounds", 5)
MSG_KEY = "session:{sid}:messages"
SUMMARY_KEY = "session:{sid}:summary"
MSG_TTL = 3600 * 24  # 24 hours


class SessionMemory:
    """Per-session memory backed by Redis."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._redis = get_redis()

    def _msg_key(self) -> str:
        return MSG_KEY.format(sid=self.session_id)

    def _summary_key(self) -> str:
        return SUMMARY_KEY.format(sid=self.session_id)

    async def add_turn(self, user_msg: str, assistant_msg: str) -> None:
        """Append a conversation turn (user + assistant) to the window."""
        pair = json.dumps({"user": user_msg, "assistant": assistant_msg}, ensure_ascii=False)
        await self._redis.rpush(self._msg_key(), pair)
        await self._redis.expire(self._msg_key(), MSG_TTL)
        logger.info("turn_added", session_id=self.session_id)

    async def get_window(self) -> list[dict]:
        """Get the recent sliding window messages as [{role, content}, ...]."""
        raw = await self._redis.lrange(self._msg_key(), 0, -1)
        messages = []
        for item in raw:
            pair = json.loads(item)
            messages.append({"role": "user", "content": pair["user"]})
            messages.append({"role": "assistant", "content": pair["assistant"]})
        return messages

    async def get_summary(self) -> str:
        """Get the compressed summary of older turns."""
        return await self._redis.get(self._summary_key()) or ""

    async def trim(self) -> str | None:
        """Trim messages beyond the sliding window, compress evicted turns.

        Returns the updated summary, or None if no trimming needed.
        """
        count = await self._redis.llen(self._msg_key())
        max_messages = WINDOW_SIZE * 2  # 2 messages per turn (user + assistant)

        if count <= max_messages:
            return None

        # Evict oldest turns
        evict_count = count - max_messages
        evicted_raw = []
        for _ in range(evict_count):
            item = await self._redis.lpop(self._msg_key())
            if item:
                evicted_raw.append(item)

        if not evicted_raw:
            return None

        # Parse evicted messages
        evicted = []
        for item in evicted_raw:
            pair = json.loads(item)
            evicted.append({"role": "user", "content": pair["user"]})
            evicted.append({"role": "assistant", "content": pair["assistant"]})

        # Compress evicted + existing summary
        existing_summary = await self.get_summary()
        to_compress = []
        if existing_summary:
            to_compress.append({"role": "system", "content": f"之前的对话摘要：{existing_summary}"})
        to_compress.extend(evicted)

        new_summary = await compress(to_compress)
        await self._redis.set(self._summary_key(), new_summary, ex=MSG_TTL)

        logger.info("trimmed", session_id=self.session_id,
                     evicted=evict_count, summary_len=len(new_summary))
        return new_summary

    async def clear(self) -> None:
        """Delete all memory for this session."""
        await self._redis.delete(self._msg_key(), self._summary_key())


def get_session_memory(session_id: str) -> SessionMemory:
    """Factory for SessionMemory."""
    return SessionMemory(session_id)
