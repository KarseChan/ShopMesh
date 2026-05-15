"""Input Guard — input safety layer.

Checks:
- Prompt Injection detection (keyword filtering + output validation)
- Input length limit (≤ 500 chars)
- Intent whitelist (only shopping-related intents allowed)
"""

import re

from src.observability.logger import get_logger

logger = get_logger("input_guard")

MAX_INPUT_LENGTH = 500

# Prompt injection patterns (common attack vectors)
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"忽略.{0,10}(之前|以前|以上).{0,10}(指令|提示|规则)",
    r"你现在是",
    r"你是一个.{0,20}(黑客|攻击|恶意)",
    r"system\s*prompt",
    r"reveal\s+(your|the)\s+(prompt|instructions)",
    r"输出.{0,5}(系统|原始|完整).{0,5}(提示|指令|prompt)",
    r"jailbreak",
    r"DAN\s*mode",
    r"pretend\s+you\s+are",
    r"act\s+as\s+(if|though)",
    r"<\|im_start\|>",
    r"<\|im_end\|>",
]

# Shopping-related intent whitelist
ALLOWED_INTENTS = {
    "search", "compare", "recommend", "detail", "order",
    "general", "clarify", "greeting", "help",
}


class InputViolation(Exception):
    """Raised when input fails safety checks."""

    def __init__(self, reason: str, severity: str = "block"):
        self.reason = reason
        self.severity = severity  # "block" | "warn"
        super().__init__(reason)


def check_input_length(text: str) -> None:
    """Check if input exceeds max length."""
    if len(text) > MAX_INPUT_LENGTH:
        raise InputViolation(
            f"输入过长（{len(text)} 字符），最多 {MAX_INPUT_LENGTH} 字符",
            severity="block",
        )


def check_injection(text: str) -> None:
    """Detect potential prompt injection attempts."""
    text_lower = text.lower()
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            logger.warning("injection_detected", pattern=pattern, text_preview=text[:50])
            raise InputViolation(
                "检测到不安全的输入内容",
                severity="block",
            )


def check_intent_whitelist(intent: str) -> None:
    """Validate intent is in the allowed whitelist."""
    if intent and intent not in ALLOWED_INTENTS:
        logger.warning("intent_not_in_whitelist", intent=intent)
        raise InputViolation(
            f"不支持的意图类型：{intent}",
            severity="warn",
        )


def validate_input(text: str, intent: str = "") -> dict:
    """Run all input safety checks.

    Returns:
        {"safe": True} if all checks pass

    Raises:
        InputViolation if any check fails
    """
    check_input_length(text)
    check_injection(text)
    if intent:
        check_intent_whitelist(intent)

    return {"safe": True}
