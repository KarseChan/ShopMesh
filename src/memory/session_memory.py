"""L2 Session Memory — sliding window + LLM summary via Redis.

L2a: Recent N turns stored as a Redis List.
L2b: Older turns compressed into a summary string.

Storage keys:
    tenant:{tenant_id}:session:{session_id}:messages  — List of JSON-encoded messages (L2a)
    tenant:{tenant_id}:session:{session_id}:summary   — Compressed summary string (L2b)
"""

import json

from src.auth.context import get_tenant_id
from src.db.redis_client import get_redis
from src.memory.compressor import compress
from src.observability.logger import get_logger
from src.config import config

logger = get_logger("session_memory")

WINDOW_SIZE: int = config.get("memory", {}).get("sliding_window_rounds", 5)
MSG_KEY = "tenant:{tid}:session:{sid}:messages"
SUMMARY_KEY = "tenant:{tid}:session:{sid}:summary"
MSG_TTL = 3600 * 24  # 24 hours

# Lua script: atomically verify head → LTRIM + SET summary
# Keys: [msg_key, summary_key]
# Args: [evict_count, new_summary, ttl, expected_head_json]
_TRIM_LUA = """
local msg_key = KEYS[1]
local summary_key = KEYS[2]
local evict_count = tonumber(ARGV[1])
local new_summary = ARGV[2]
local ttl = tonumber(ARGV[3])
local expected_json = ARGV[4]

-- Get current head of list
local current_head = redis.call('LRANGE', msg_key, 0, evict_count - 1)

-- Parse expected head from JSON
local expected = cjson.decode(expected_json)

-- Verify head matches (same messages in same order)
if #current_head ~= #expected then
    return 0
end
for i = 1, #current_head do
    if current_head[i] ~= expected[i] then
        return 0
    end
end

-- Head matches: atomically trim + update summary
redis.call('LTRIM', msg_key, evict_count, -1)
redis.call('SET', summary_key, new_summary, 'EX', ttl)
return 1
"""


class SessionMemory:
    """Per-session memory backed by Redis."""

    def __init__(self, session_id: str, tenant_id: str = ""):
        self.session_id = session_id
        self.tenant_id = tenant_id or get_tenant_id()
        self._redis = get_redis()

    def _msg_key(self) -> str:
        return MSG_KEY.format(tid=self.tenant_id, sid=self.session_id)

    def _summary_key(self) -> str:
        return SUMMARY_KEY.format(tid=self.tenant_id, sid=self.session_id)

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

        Race-condition safe implementation:
        1. LRANGE to copy (not pop) the oldest N messages
        2. LLM compress (1~3s, List untouched during this time)
        3. Lua script atomically: verify head still matches → LTRIM + SET summary

        If head changed during LLM compression (e.g. new RPUSH happened),
        the Lua script returns 0 and we abort — next trim call will retry.

        Returns the updated summary, or None if no trimming needed.
        """
        msg_key = self._msg_key()
        summary_key = self._summary_key()
        count = await self._redis.llen(msg_key)
        max_messages = WINDOW_SIZE * 2  # 2 messages per turn (user + assistant)

        if count <= max_messages:
            return None

        # Step 1: LRANGE copy (does NOT modify the List)
        evict_count = count - max_messages
        evicted_raw = await self._redis.lrange(msg_key, 0, evict_count - 1)
        if not evicted_raw:
            return None

        # Step 2: LLM compression (slow, 1~3s — List can be RPUSHed during this)
        evicted = []
        for item in evicted_raw:
            pair = json.loads(item)
            evicted.append({"role": "user", "content": pair["user"]})
            evicted.append({"role": "assistant", "content": pair["assistant"]})

        existing_summary = await self.get_summary()
        to_compress = []
        if existing_summary:
            to_compress.append({"role": "system", "content": f"之前的对话摘要：{existing_summary}"})
        to_compress.extend(evicted)

        new_summary = await compress(to_compress)

        # Step 3: Lua script atomic verify + trim
        expected_json = json.dumps(evicted_raw, ensure_ascii=False)
        result = await self._redis.eval(
            _TRIM_LUA,
            2,  # number of keys
            msg_key, summary_key,
            evict_count, new_summary, MSG_TTL, expected_json,
        )

        if result == 1:
            logger.info("trimmed", session_id=self.session_id,
                         evicted=evict_count, summary_len=len(new_summary))
            return new_summary
        else:
            # Head changed during LLM compression — abort, retry next time
            logger.warning("trim_aborted", session_id=self.session_id,
                           reason="head_changed_during_compression")
            return None

    async def clear(self) -> None:
        """Delete all memory for this session."""
        await self._redis.delete(self._msg_key(), self._summary_key())


def get_session_memory(session_id: str, tenant_id: str = "") -> SessionMemory:
    """Factory for SessionMemory."""
    return SessionMemory(session_id, tenant_id=tenant_id)
