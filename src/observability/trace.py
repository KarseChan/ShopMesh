"""Request tracing decorator — auto-record module / step / duration_ms.

Usage:
    from src.observability.trace import traced

    @traced("ranker", "ranking_done")
    async def rank_items(state):
        ...
"""

import functools
import time

from src.observability.logger import get_logger


def traced(module: str, step: str):
    """Decorator that logs start/end of a step with duration_ms."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            logger = get_logger(module)
            logger.info(f"{step}_start", step=step)
            start = time.monotonic()
            try:
                result = await func(*args, **kwargs)
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(step, step=step, duration_ms=duration_ms)
                return result
            except Exception as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.error(f"{step}_error", step=step, duration_ms=duration_ms,
                             error_type=type(e).__name__, error_message=str(e))
                raise
        return wrapper
    return decorator
