"""Error logger with enhanced fields — error_type / error_code / fallback_action / attempt / degraded.

Usage:
    from src.observability.error_logger import log_error
    log_error(module="llm_router", step="classify", error=e, attempt=2, fallback_action="retry_with_backoff")
"""

from src.observability.logger import get_logger


def log_error(
    module: str,
    step: str,
    error: Exception,
    attempt: int = 1,
    fallback_action: str = "",
    error_code: int | str = "",
    degraded: bool = False,
):
    """Log an error with enhanced diagnostic fields."""
    logger = get_logger(module)
    logger.error(
        "error",
        step=step,
        error_type=type(error).__name__,
        error_code=error_code,
        error_message=str(error),
        attempt=attempt,
        fallback_action=fallback_action,
        degraded=degraded,
    )
