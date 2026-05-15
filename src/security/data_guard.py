"""Data Guard — data safety layer.

Checks:
- Session isolation: each user has independent state
- Address masking: hide house numbers in display
- Payment info: Agent never stores card numbers
- Log sanitization: sensitive fields auto-masked
"""

import re

from src.observability.logger import get_logger

logger = get_logger("data_guard")

# Patterns for sensitive data
_CARD_PATTERN = re.compile(r"\b(\d{4})\d{8,12}(\d{4})\b")
_PHONE_PATTERN = re.compile(r"\b(1[3-9]\d)\d{4}(\d{4})\b")
_ID_PATTERN = re.compile(r"\b(\d{6})\d{8}(\d{4})\b")

# Fields that should never appear in logs
_SENSITIVE_FIELDS = {"card_number", "cvv", "password", "id_number", "token", "secret"}


def mask_address(address: str) -> str:
    """Mask house number in address for display.

    "北京市朝阳区建国路88号" → "北京市朝阳区建国路**号"
    """
    if not address:
        return address

    # Mask numbers before 号/楼/室/层
    masked = re.sub(r"(\d+)(号|楼|室|层|栋|单元)", lambda m: "**" + m.group(2), address)
    return masked


def mask_card_number(card: str) -> str:
    """Mask card number: show first 4 and last 4."""
    return _CARD_PATTERN.sub(r"\1****\2****\2", card)


def mask_phone(phone: str) -> str:
    """Mask phone number: show first 3 and last 4."""
    return _PHONE_PATTERN.sub(r"\1****\2", phone)


def mask_id_number(id_num: str) -> str:
    """Mask ID number: show first 6 and last 4."""
    return _ID_PATTERN.sub(r"\1********\2", id_num)


def sanitize_for_log(data: dict) -> dict:
    """Remove or mask sensitive fields before logging.

    Creates a shallow copy with sensitive fields replaced by "[REDACTED]".
    """
    sanitized = {}
    for key, value in data.items():
        if key in _SENSITIVE_FIELDS:
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, str):
            # Mask any embedded sensitive patterns
            v = _CARD_PATTERN.sub(r"\1****\2", value)
            v = _PHONE_PATTERN.sub(r"\1****\2", v)
            sanitized[key] = v
        else:
            sanitized[key] = value
    return sanitized


def validate_session_isolation(user_id: str, session_id: str) -> dict:
    """Validate that session belongs to user (placeholder for real auth).

    Returns:
        {"valid": True} if session is valid
    """
    # In production: verify session ownership via auth service
    # MVP: always valid
    if not user_id or not session_id:
        logger.warning("missing_session_context", user_id=user_id, session_id=session_id)
    return {"valid": True}


def check_payment_info_not_stored(data: dict) -> bool:
    """Verify no payment card data is present in the data dict.

    Returns True if clean, False if card data detected.
    """
    for key in _SENSITIVE_FIELDS:
        if key in data:
            logger.warning("payment_data_detected", field=key)
            return False
    return True
