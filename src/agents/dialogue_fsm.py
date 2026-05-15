"""Dialogue FSM — finite state machine managing the conversation lifecycle.

States:
    idle → intent_detected → clarifying → searching → presenting → ordering

Features:
- Intent switch detection with user confirmation
- Rollback to last stable state on failure
- Debounce: rapid consecutive inputs merged
"""

import time
from enum import Enum
from typing import Any

from src.observability.logger import get_logger

logger = get_logger("dialogue_fsm")


class State(str, Enum):
    IDLE = "idle"
    INTENT_DETECTED = "intent_detected"
    CLARIFYING = "clarifying"
    SEARCHING = "searching"
    PRESENTING = "presenting"
    ORDERING = "ordering"


# Valid transitions: (from_state, to_state)
VALID_TRANSITIONS = {
    (State.IDLE, State.INTENT_DETECTED),
    (State.INTENT_DETECTED, State.CLARIFYING),
    (State.INTENT_DETECTED, State.SEARCHING),
    (State.CLARIFYING, State.SEARCHING),
    (State.CLARIFYING, State.INTENT_DETECTED),
    (State.SEARCHING, State.PRESENTING),
    (State.PRESENTING, State.ORDERING),
    (State.PRESENTING, State.INTENT_DETECTED),
    (State.ORDERING, State.IDLE),
    # Error rollback paths
    (State.CLARIFYING, State.IDLE),
    (State.SEARCHING, State.IDLE),
    (State.SEARCHING, State.PRESENTING),
    (State.ORDERING, State.PRESENTING),
    # Allow reset from any state
    (State.INTENT_DETECTED, State.IDLE),
    (State.PRESENTING, State.IDLE),
}

# States where the system waits for user input (stable resting points)
STABLE_STATES = {State.IDLE, State.PRESENTING, State.ORDERING}

# Debounce window in seconds
DEBOUNCE_WINDOW = 0.5


class DialogueFSM:
    """Manages dialogue state transitions with intent-switch detection and rollback."""

    def __init__(self):
        self._state = State.IDLE
        self._prev_stable = State.IDLE
        self._history: list[dict] = []
        self._last_input_time: float = 0
        self._pending_input: str | None = None
        self._current_intent: str | None = None
        self._confirm_pending: dict | None = None

    @property
    def state(self) -> State:
        return self._state

    @property
    def current_intent(self) -> str | None:
        return self._current_intent

    @property
    def is_stable(self) -> bool:
        return self._state in STABLE_STATES

    @property
    def confirm_pending(self) -> dict | None:
        """Returns pending intent-switch confirmation request, or None."""
        return self._confirm_pending

    def transition(self, to_state: State, intent: str | None = None,
                   reason: str = "") -> bool:
        """Attempt a state transition.

        Returns True if transition succeeded, False if invalid.
        """
        key = (self._state, to_state)
        if key not in VALID_TRANSITIONS:
            logger.warning("invalid_transition", from_state=self._state.value,
                           to_state=to_state.value)
            return False

        old_state = self._state
        self._state = to_state

        if intent:
            self._current_intent = intent

        # Track stable states for rollback (save previous before overwriting)
        if to_state in STABLE_STATES and old_state in STABLE_STATES:
            self._prev_stable = old_state
        elif to_state in STABLE_STATES:
            pass  # keep existing _prev_stable if coming from unstable state

        self._history.append({
            "from": old_state.value,
            "to": to_state.value,
            "intent": intent,
            "reason": reason,
            "timestamp": time.time(),
        })

        logger.info("state_transition", from_state=old_state.value,
                     to_state=to_state.value, intent=intent, reason=reason)
        return True

    def rollback(self, reason: str = "error") -> State:
        """Rollback to the last stable state.

        Returns the new current state.
        """
        old = self._state
        self._state = self._prev_stable
        self._confirm_pending = None

        self._history.append({
            "from": old.value,
            "to": self._state.value,
            "intent": None,
            "reason": f"rollback:{reason}",
            "timestamp": time.time(),
        })

        logger.info("state_rollback", from_state=old.value,
                     to_state=self._state.value, reason=reason)
        return self._state

    def check_intent_switch(self, new_intent: str) -> dict | None:
        """Check if a new intent conflicts with the current state.

        Returns a confirmation request dict if switch detected, else None.
        Format: {"message": str, "current_state": str, "new_intent": str}
        """
        if self._state == State.IDLE:
            return None

        if self._current_intent and new_intent != self._current_intent:
            # Intent switch detected
            self._confirm_pending = {
                "message": f"你正在{self._state_description()}，要切换到{self._intent_description(new_intent)}吗？",
                "current_state": self._state.value,
                "new_intent": new_intent,
            }
            logger.info("intent_switch_detected",
                         current_intent=self._current_intent,
                         new_intent=new_intent,
                         current_state=self._state.value)
            return self._confirm_pending

        return None

    def confirm_switch(self, accept: bool, new_intent: str | None = None) -> State:
        """Handle user's response to intent-switch confirmation.

        If accept: transition to intent_detected with new intent.
        If reject: stay in current state.
        """
        self._confirm_pending = None
        if accept:
            self.transition(State.IDLE, reason="user_confirmed_switch")
            if new_intent:
                self.transition(State.INTENT_DETECTED, intent=new_intent,
                               reason="switched_to_new_intent")
        return self._state

    def debounce_input(self, user_input: str) -> str | None:
        """Handle input debouncing for rapid consecutive messages.

        Returns the merged input if debounce triggers, None to process immediately.
        """
        now = time.time()
        elapsed = now - self._last_input_time

        if elapsed < DEBOUNCE_WINDOW and self._pending_input:
            # Merge inputs
            self._pending_input = f"{self._pending_input}\n{user_input}"
            logger.info("input_debounced", elapsed_ms=int(elapsed * 1000))
            return None  # Still debouncing, don't process yet

        # Flush pending if exists
        result = self._pending_input
        self._pending_input = user_input
        self._last_input_time = now

        if result:
            return result  # Return the accumulated input
        return None  # First input in the window, wait for more

    def flush(self) -> str | None:
        """Flush any pending debounced input.

        Returns the pending input or None.
        """
        result = self._pending_input
        self._pending_input = None
        return result

    def reset(self) -> None:
        """Reset to idle state."""
        old = self._state
        self._state = State.IDLE
        self._current_intent = None
        self._confirm_pending = None
        self._pending_input = None
        self._prev_stable = State.IDLE
        self._history.append({
            "from": old.value,
            "to": State.IDLE.value,
            "intent": None,
            "reason": "reset",
            "timestamp": time.time(),
        })
        logger.info("state_reset", from_state=old.value)

    def get_history(self) -> list[dict]:
        """Get the transition history."""
        return list(self._history)

    def _state_description(self) -> str:
        descriptions = {
            State.IDLE: "等待输入",
            State.INTENT_DETECTED: "理解意图中",
            State.CLARIFYING: "询问需求",
            State.SEARCHING: "搜索商品",
            State.PRESENTING: "展示结果",
            State.ORDERING: "下单流程",
        }
        return descriptions.get(self._state.value, self._state.value)

    def _intent_description(self, intent: str) -> str:
        descriptions = {
            "search": "搜索商品",
            "compare": "比较商品",
            "recommend": "推荐商品",
            "detail": "查看详情",
            "order": "下单",
        }
        return descriptions.get(intent, intent)
