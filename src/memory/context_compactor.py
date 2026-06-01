"""Context Compactor — four-layer compression pipeline for message lists.

Implements the "cheap first, expensive later" strategy:
- L1 snip_compact: Truncate old messages, keep head + tail (0 API)
- L2 micro_compact: Replace old tool_results with placeholders (0 API)
- L3 tool_result_budget: Persist large outputs to Redis (0 API)
- L4 compact_history: LLM-generated summary (1 API)

Includes a circuit breaker to stop compaction after repeated failures.
"""

import json

from src.memory.compressor import compress, estimate_tokens
from src.observability.logger import get_logger

logger = get_logger("context_compactor")

# Default thresholds
_DEFAULT_MAX_MESSAGES = 50
_DEFAULT_KEEP_RECENT_TOOL_RESULTS = 3
_DEFAULT_MAX_TOOL_RESULT_BYTES = 200_000
_DEFAULT_TOKEN_THRESHOLD = 6000
_DEFAULT_PREVIEW_CHARS = 2000


class CompactionCircuitBreaker:
    """Stops compaction after repeated failures to prevent infinite loops."""

    MAX_FAILURES = 3

    def __init__(self):
        self.failure_count = 0

    def record_failure(self):
        self.failure_count += 1
        logger.warning("compaction_failure_recorded",
                        count=self.failure_count,
                        max=self.MAX_FAILURES)

    def is_open(self) -> bool:
        return self.failure_count >= self.MAX_FAILURES

    def reset(self):
        self.failure_count = 0


class ContextCompactor:
    """Four-layer context compression pipeline."""

    def __init__(self, redis_client=None, session_id: str = ""):
        self._redis = redis_client
        self._session_id = session_id

    # ── L1: snip_compact ──

    def snip_compact(self, messages: list[dict],
                     max_messages: int = _DEFAULT_MAX_MESSAGES) -> list[dict]:
        """Truncate old messages, keep head (3) + tail (max_messages - 3).

        Zero API calls. Preserves system prompt and recent context.
        """
        if len(messages) <= max_messages:
            return messages

        keep_head = 3
        keep_tail = max_messages - keep_head
        snipped = len(messages) - keep_head - keep_tail

        placeholder = {
            "role": "user",
            "content": f"[已精简 {snipped} 条历史消息]",
        }

        result = messages[:keep_head] + [placeholder] + messages[-keep_tail:]
        logger.info("snip_compact_applied",
                     original=len(messages),
                     snipped=snipped,
                     remaining=len(result))
        return result

    # ── L2: micro_compact ──

    def micro_compact(self, messages: list[dict],
                      keep_recent: int = _DEFAULT_KEEP_RECENT_TOOL_RESULTS) -> list[dict]:
        """Replace old tool_result content with placeholders.

        Zero API calls. Only keeps the most recent tool_results intact.
        """
        # Collect all tool_result blocks with their positions
        tool_results = []
        for i, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for j, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append((i, j, block))

        if len(tool_results) <= keep_recent:
            return messages

        # Replace old tool_results with placeholders
        compacted_count = 0
        for i, j, block in tool_results[:-keep_recent]:
            content_str = str(block.get("content", ""))
            if len(content_str) > 120:
                block["content"] = "[早期工具结果已压缩]"
                compacted_count += 1

        if compacted_count > 0:
            logger.info("micro_compact_applied",
                         total_tool_results=len(tool_results),
                         compacted=compacted_count,
                         kept=keep_recent)

        return messages

    # ── L3: tool_result_budget ──

    async def tool_result_budget(
        self,
        messages: list[dict],
        max_bytes: int = _DEFAULT_MAX_TOOL_RESULT_BYTES,
        preview_chars: int = _DEFAULT_PREVIEW_CHARS,
    ) -> list[dict]:
        """Persist large tool outputs to Redis, keep preview in context.

        Zero API calls. Requires redis_client and session_id set in __init__.
        """
        if not self._redis or not self._session_id:
            return messages

        # Find the last user message with tool_results
        last_user_msg = None
        for msg in reversed(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                last_user_msg = msg
                break

        if not last_user_msg:
            return messages

        # Calculate total size of tool_results
        blocks = []
        for block in last_user_msg["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                blocks.append(block)

        if not blocks:
            return messages

        total_size = sum(len(str(b.get("content", ""))) for b in blocks)
        if total_size <= max_bytes:
            return messages

        # Persist largest results first until under budget
        sorted_blocks = sorted(
            blocks,
            key=lambda b: len(str(b.get("content", ""))),
            reverse=True,
        )

        persisted_count = 0
        for block in sorted_blocks:
            if total_size <= max_bytes:
                break

            content_str = str(block.get("content", ""))
            if len(content_str) <= preview_chars:
                continue

            # Persist to Redis
            tool_use_id = block.get("tool_use_id", "unknown")
            key = f"tool_result:{self._session_id}:{tool_use_id}"
            try:
                await self._redis.set(key, content_str, ex=3600 * 24)  # 24h TTL
                # Replace with preview
                preview = content_str[:preview_chars]
                block["content"] = f"{preview}\n[完整结果已持久化，ID: {tool_use_id}]"
                total_size -= len(content_str) - len(block["content"])
                persisted_count += 1
            except Exception as e:
                logger.warning("tool_result_persist_failed",
                                tool_use_id=tool_use_id, error=str(e))

        if persisted_count > 0:
            logger.info("tool_result_budget_applied",
                         persisted=persisted_count,
                         remaining_size=total_size)

        return messages

    # ── L4: compact_history ──

    async def compact_history(self, messages: list[dict]) -> list[dict]:
        """LLM-generated summary of conversation history.

        One API call. Falls back to simple truncation on failure.
        """
        if len(messages) <= 6:
            return messages

        try:
            # Keep system prompt + last 2 messages, compress the rest
            system = messages[0] if messages[0].get("role") == "system" else None
            to_compress = messages[1:-2] if system else messages[:-2]
            recent = messages[-2:]

            if not to_compress:
                return messages

            summary = await compress(to_compress)

            result = []
            if system:
                result.append(system)
            result.append({
                "role": "user",
                "content": f"[上下文已压缩]\n\n{summary}",
            })
            result.extend(recent)

            logger.info("compact_history_applied",
                         original=len(messages),
                         compressed=len(to_compress),
                         result=len(result))
            return result

        except Exception as e:
            logger.error("compact_history_failed", error=str(e))
            # Fallback: aggressive snip
            return self.snip_compact(messages, max_messages=10)

    # ── Combined pipeline ──

    async def apply_compaction(
        self,
        messages: list[dict],
        token_threshold: int = _DEFAULT_TOKEN_THRESHOLD,
    ) -> list[dict]:
        """Apply four-layer compaction pipeline: L3 → L1 → L2 → L4.

        Order matters: L3 (budget) must run before L2 (micro) because
        micro replaces old tool_results with placeholders, and budget
        needs the full content to persist it first.
        """
        # Estimate current tokens
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        estimated_tokens = total_chars // 3  # rough estimate

        if estimated_tokens < token_threshold:
            return messages

        logger.info("compaction_start",
                     messages=len(messages),
                     estimated_tokens=estimated_tokens,
                     threshold=token_threshold)

        # L3: Persist large tool results (0 API)
        messages = await self.tool_result_budget(messages)

        # L1: Truncate old messages (0 API)
        messages = self.snip_compact(messages)

        # L2: Replace old tool_results (0 API)
        messages = self.micro_compact(messages)

        # Re-estimate after L1-L3
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        estimated_tokens = total_chars // 3

        # L4: LLM summary if still over threshold (1 API)
        if estimated_tokens > token_threshold:
            logger.info("compaction_llm_triggered",
                         post_l123_tokens=estimated_tokens)
            messages = await self.compact_history(messages)

        return messages


def get_compactor(redis_client=None, session_id: str = "") -> ContextCompactor:
    """Factory for ContextCompactor."""
    return ContextCompactor(redis_client=redis_client, session_id=session_id)
