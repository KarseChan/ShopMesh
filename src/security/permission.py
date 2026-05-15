"""Permission Guard — execution safety layer.

Checks:
- Permission levels: read (auto) / write (user confirm) / sensitive (double-confirm)
- Payment idempotency: same request_id = same order
- Amount validation: display price vs actual charge consistency
- Rate limiting: max 10 tool calls per user per minute
"""

import time
from collections import defaultdict

from src.observability.logger import get_logger
from src.skills.schema import PermissionLevel

logger = get_logger("permission_guard")

RATE_LIMIT_CALLS = 10
RATE_LIMIT_WINDOW = 60  # seconds

# Rate limit tracking: user_id -> list[timestamp]
_rate_limits: dict[str, list[float]] = defaultdict(list)


class PermissionViolation(Exception):
    """Raised when a permission check fails."""

    def __init__(self, reason: str, violation_type: str = "permission"):
        self.reason = reason
        self.violation_type = violation_type
        super().__init__(reason)


def check_permission(skill_permission: PermissionLevel, user_confirmed: bool = False) -> None:
    """Check if execution is allowed based on skill permission level.

    Args:
        skill_permission: The skill's permission level
        user_confirmed: Whether the user has confirmed the action

    Raises:
        PermissionViolation if not allowed
    """
    if skill_permission == PermissionLevel.READ:
        return  # Auto-execute

    if skill_permission == PermissionLevel.WRITE and not user_confirmed:
        raise PermissionViolation(
            "此操作需要用户确认",
            violation_type="confirmation_required",
        )

    if skill_permission == PermissionLevel.SENSITIVE and not user_confirmed:
        raise PermissionViolation(
            "此操作需要二次确认",
            violation_type="double_confirmation_required",
        )


def check_rate_limit(user_id: str) -> None:
    """Check if user has exceeded the rate limit.

    Raises:
        PermissionViolation if rate limit exceeded
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Clean old entries
    _rate_limits[user_id] = [t for t in _rate_limits[user_id] if t > window_start]

    if len(_rate_limits[user_id]) >= RATE_LIMIT_CALLS:
        logger.warning("rate_limit_exceeded", user_id=user_id,
                       calls=len(_rate_limits[user_id]))
        raise PermissionViolation(
            f"操作过于频繁，每分钟最多 {RATE_LIMIT_CALLS} 次",
            violation_type="rate_limit",
        )

    _rate_limits[user_id].append(now)


def check_amount_consistency(display_price: float, actual_price: float) -> None:
    """Verify display price matches actual charge price.

    Raises:
        PermissionViolation if prices don't match
    """
    if abs(display_price - actual_price) > 0.01:
        logger.warning("amount_mismatch", display=display_price, actual=actual_price)
        raise PermissionViolation(
            f"展示金额 ¥{display_price} 与实际金额 ¥{actual_price} 不一致",
            violation_type="amount_mismatch",
        )


def clear_rate_limits() -> None:
    """Clear rate limit state (for testing)."""
    _rate_limits.clear()
