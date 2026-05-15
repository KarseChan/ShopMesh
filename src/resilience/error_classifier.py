"""Error Classifier — categorize errors for appropriate handling.

Categories:
- silent: user-unaware (format retry, memory degradation, tool cache fallback)
- user_visible: user sees message with action guidance
- user_action: user must take action (re-enter input, confirm payment)
"""

from enum import Enum

from src.observability.logger import get_logger

logger = get_logger("error_classifier")


class ErrorCategory(str, Enum):
    SILENT = "silent"
    USER_VISIBLE = "user_visible"
    USER_ACTION = "user_action"


class ErrorSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Error type → (category, severity, user_message)
_ERROR_MAP: dict[str, tuple[ErrorCategory, ErrorSeverity, str]] = {
    # LLM layer
    "json_parse": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "llm_429": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "llm_503": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "llm_timeout": (ErrorCategory.SILENT, ErrorSeverity.MEDIUM, ""),
    "llm_401": (ErrorCategory.USER_VISIBLE, ErrorSeverity.HIGH, "服务暂时不可用，请稍后重试"),
    "content_rejected": (ErrorCategory.USER_ACTION, ErrorSeverity.MEDIUM, "请重新描述您的需求"),

    # Tool layer
    "tool_timeout": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "tool_connection": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "tool_5xx": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "tool_persistent_failure": (ErrorCategory.USER_VISIBLE, ErrorSeverity.MEDIUM, "商品数据加载中，请稍后再试"),

    # Memory layer
    "redis_timeout": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "qdrant_timeout": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),
    "memory_degraded": (ErrorCategory.SILENT, ErrorSeverity.LOW, ""),

    # Security
    "input_violation": (ErrorCategory.USER_ACTION, ErrorSeverity.MEDIUM, "输入内容不安全，请重新输入"),
    "output_violation": (ErrorCategory.SILENT, ErrorSeverity.HIGH, ""),
    "permission_violation": (ErrorCategory.USER_ACTION, ErrorSeverity.MEDIUM, "此操作需要确认"),
    "rate_limit": (ErrorCategory.USER_VISIBLE, ErrorSeverity.MEDIUM, "操作过于频繁，请稍后再试"),

    # Order
    "order_failed": (ErrorCategory.USER_VISIBLE, ErrorSeverity.HIGH, "下单失败，请重试"),
    "payment_mismatch": (ErrorCategory.USER_ACTION, ErrorSeverity.CRITICAL, "金额异常，请联系客服"),
}


def classify_error(error_type: str, default_category: ErrorCategory = ErrorCategory.SILENT) -> dict:
    """Classify an error type into category, severity, and user message.

    Returns:
        {"category": ErrorCategory, "severity": ErrorSeverity, "user_message": str}
    """
    if error_type in _ERROR_MAP:
        cat, sev, msg = _ERROR_MAP[error_type]
        return {"category": cat, "severity": sev, "user_message": msg}

    logger.info("unknown_error_type", error_type=error_type)
    return {
        "category": default_category,
        "severity": ErrorSeverity.MEDIUM,
        "user_message": "发生未知错误，请重试",
    }


def is_silent(error_type: str) -> bool:
    """Check if error should be handled silently."""
    classification = classify_error(error_type)
    return classification["category"] == ErrorCategory.SILENT


def get_user_message(error_type: str) -> str:
    """Get user-facing error message."""
    classification = classify_error(error_type)
    return classification["user_message"]
