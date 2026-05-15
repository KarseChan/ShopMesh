"""Tool Retry — retry handler for tool calls.

Features:
- Max 2 retries with fixed 500ms interval
- Cache fallback on persistent failure
- Degraded response instead of blocking
"""

import asyncio

from src.observability.logger import get_logger

logger = get_logger("tool_retry")

MAX_RETRIES = 2
RETRY_INTERVAL = 0.5  # seconds


class ToolRetryExhausted(Exception):
    """All retry attempts exhausted for a tool call."""

    def __init__(self, tool_name: str, last_error: Exception, attempts: int):
        self.tool_name = tool_name
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"Tool {tool_name} failed after {attempts} attempts: {last_error}")


async def retry_tool_call(
    call_fn,
    *args,
    tool_name: str = "unknown",
    max_retries: int = MAX_RETRIES,
    fallback_fn=None,
    **kwargs,
):
    """Retry an async tool call with fixed interval.

    Args:
        call_fn: Async function to call
        tool_name: Name for logging
        max_retries: Maximum retry attempts
        fallback_fn: Optional async fallback function on exhaustion
        *args, **kwargs: Passed to call_fn

    Returns:
        The result of call_fn on success, or fallback_fn result if exhausted
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            result = await call_fn(*args, **kwargs)
            if attempt > 0:
                logger.info("tool_retry_success", tool=tool_name, attempt=attempt + 1)
            return result

        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Network/timeout errors — retry
            if any(kw in error_str for kw in ["timeout", "connection", "5xx", "502", "503", "reset"]):
                logger.warning("tool_retry", tool=tool_name, attempt=attempt + 1, error=str(e))
                await asyncio.sleep(RETRY_INTERVAL)
                continue

            # Other errors — don't retry
            logger.error("tool_error", tool=tool_name, error=str(e))
            raise ToolRetryExhausted(tool_name, e, attempt + 1)

    # All retries exhausted — try fallback
    if fallback_fn:
        logger.info("tool_fallback", tool=tool_name)
        try:
            return await fallback_fn(*args, **kwargs)
        except Exception as fallback_error:
            logger.error("tool_fallback_failed", tool=tool_name, error=str(fallback_error))

    raise ToolRetryExhausted(tool_name, last_error, max_retries)
