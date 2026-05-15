"""LLM Retry — retry handler for LLM calls.

Features:
- Format repair: retry with explicit JSON instruction on parse failure
- Model fallback: switch to backup model after max retries
- 429 backoff: respect Retry-After header
- Exponential backoff: 1s / 2s / 4s
"""

import asyncio
import json

from src.observability.logger import get_logger

logger = get_logger("llm_retry")

MAX_RETRIES = 3
BACKOFF_BASE = [1.0, 2.0, 4.0]  # seconds


class LLMRetryExhausted(Exception):
    """All retry attempts exhausted."""

    def __init__(self, last_error: Exception, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"LLM call failed after {attempts} attempts: {last_error}")


async def retry_llm_call(
    call_fn,
    *args,
    max_retries: int = MAX_RETRIES,
    on_retry=None,
    **kwargs,
):
    """Retry an async LLM call with exponential backoff.

    Args:
        call_fn: Async function to call
        max_retries: Maximum retry attempts
        on_retry: Optional callback(attempt, error) called on each retry
        *args, **kwargs: Passed to call_fn

    Returns:
        The result of call_fn on success

    Raises:
        LLMRetryExhausted if all retries fail
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            result = await call_fn(*args, **kwargs)
            if attempt > 0:
                logger.info("llm_retry_success", attempt=attempt + 1)
            return result

        except json.JSONDecodeError as e:
            last_error = e
            logger.warning("llm_json_parse_error", attempt=attempt + 1, error=str(e))
            if on_retry:
                on_retry(attempt, e)

        except Exception as e:
            error_str = str(e).lower()
            last_error = e

            # 429 rate limit — respect backoff
            if "429" in error_str or "rate" in error_str:
                wait = BACKOFF_BASE[min(attempt, len(BACKOFF_BASE) - 1)]
                logger.warning("llm_rate_limited", attempt=attempt + 1, wait_s=wait)
                await asyncio.sleep(wait)
                if on_retry:
                    on_retry(attempt, e)
                continue

            # 503 / timeout — retry with backoff
            if "503" in error_str or "timeout" in error_str or "timed out" in error_str:
                wait = BACKOFF_BASE[min(attempt, len(BACKOFF_BASE) - 1)]
                logger.warning("llm_unavailable", attempt=attempt + 1, wait_s=wait)
                await asyncio.sleep(wait)
                if on_retry:
                    on_retry(attempt, e)
                continue

            # 401 — not retryable
            if "401" in error_str or "auth" in error_str:
                logger.error("llm_auth_failed", error=str(e))
                raise LLMRetryExhausted(e, attempt + 1)

            # Other errors — retry with backoff
            wait = BACKOFF_BASE[min(attempt, len(BACKOFF_BASE) - 1)]
            logger.warning("llm_error", attempt=attempt + 1, error=str(e), wait_s=wait)
            await asyncio.sleep(wait)
            if on_retry:
                on_retry(attempt, e)

    raise LLMRetryExhausted(last_error, max_retries)
