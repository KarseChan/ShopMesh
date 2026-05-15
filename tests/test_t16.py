"""T1.6 dialogue FSM tests."""

import time
import pytest

from src.agents.dialogue_fsm import DialogueFSM, State


def test_initial_state():
    fsm = DialogueFSM()
    assert fsm.state == State.IDLE
    assert fsm.is_stable is True
    assert fsm.current_intent is None


def test_valid_transition():
    fsm = DialogueFSM()
    result = fsm.transition(State.INTENT_DETECTED, intent="search", reason="user_query")
    assert result is True
    assert fsm.state == State.INTENT_DETECTED
    assert fsm.current_intent == "search"


def test_invalid_transition():
    fsm = DialogueFSM()
    result = fsm.transition(State.PRESENTING)
    assert result is False
    assert fsm.state == State.IDLE  # unchanged


def test_full_lifecycle():
    """Test the full happy-path lifecycle."""
    fsm = DialogueFSM()

    # idle → intent_detected
    fsm.transition(State.INTENT_DETECTED, intent="search")
    assert fsm.state == State.INTENT_DETECTED

    # intent_detected → searching (no clarification needed)
    fsm.transition(State.SEARCHING)
    assert fsm.state == State.SEARCHING

    # searching → presenting
    fsm.transition(State.PRESENTING)
    assert fsm.state == State.PRESENTING
    assert fsm.is_stable is True

    # presenting → ordering
    fsm.transition(State.ORDERING, intent="order")
    assert fsm.state == State.ORDERING

    # ordering → idle (order complete)
    fsm.transition(State.IDLE)
    assert fsm.state == State.IDLE


def test_clarification_flow():
    """Test the clarification sub-flow."""
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.CLARIFYING, reason="missing_info")
    assert fsm.state == State.CLARIFYING

    # After clarification, go to searching
    fsm.transition(State.SEARCHING, reason="clarification_done")
    assert fsm.state == State.SEARCHING


def test_rollback():
    """Rollback returns to last stable state."""
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.SEARCHING)

    # Error during search, rollback
    new_state = fsm.rollback(reason="search_failed")
    assert new_state == State.IDLE  # last stable state


def test_rollback_from_clarifying():
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.CLARIFYING)

    new_state = fsm.rollback(reason="clarification_timeout")
    assert new_state == State.IDLE


def test_rollback_preserves_stable():
    """Rollback from ordering goes back to presenting (last stable)."""
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.SEARCHING)
    fsm.transition(State.PRESENTING)
    fsm.transition(State.ORDERING)

    new_state = fsm.rollback(reason="payment_failed")
    assert new_state == State.PRESENTING


def test_intent_switch_detection():
    """Detect when user changes intent mid-conversation."""
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.SEARCHING)
    fsm.transition(State.PRESENTING)

    # User now wants to compare
    result = fsm.check_intent_switch("compare")
    assert result is not None
    assert result["new_intent"] == "compare"
    assert fsm.confirm_pending is not None


def test_intent_switch_no_conflict():
    """Same intent doesn't trigger switch detection."""
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.SEARCHING)
    fsm.transition(State.PRESENTING)

    result = fsm.check_intent_switch("search")
    assert result is None


def test_intent_switch_idle_no_switch():
    """No switch detection in idle state."""
    fsm = DialogueFSM()
    result = fsm.check_intent_switch("search")
    assert result is None


def test_confirm_switch_accept():
    """Accepting switch resets and starts new intent."""
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.SEARCHING)
    fsm.transition(State.PRESENTING)

    fsm.check_intent_switch("compare")
    new_state = fsm.confirm_switch(accept=True, new_intent="compare")
    assert new_state == State.INTENT_DETECTED
    assert fsm.current_intent == "compare"


def test_confirm_switch_reject():
    """Rejecting switch keeps current state."""
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.SEARCHING)
    fsm.transition(State.PRESENTING)

    fsm.check_intent_switch("compare")
    new_state = fsm.confirm_switch(accept=False)
    assert new_state == State.PRESENTING
    assert fsm.current_intent == "search"


def test_reset():
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search")
    fsm.transition(State.SEARCHING)
    fsm.reset()
    assert fsm.state == State.IDLE
    assert fsm.current_intent is None


def test_debounce_rapid_input():
    """Rapid inputs should be merged."""
    fsm = DialogueFSM()

    # First input
    result1 = fsm.debounce_input("我要")
    assert result1 is None  # waiting for more

    # Very quick second input
    time.sleep(0.1)
    result2 = fsm.debounce_input("奶茶")
    assert result2 is None  # still debouncing


def test_debounce_slow_input():
    """Slow inputs should not be merged."""
    fsm = DialogueFSM()

    # First input
    result1 = fsm.debounce_input("帮我找奶茶")
    assert result1 is None  # first input, wait

    # Wait longer than debounce window
    time.sleep(0.6)
    result2 = fsm.debounce_input("还要热的")
    assert result2 == "帮我找奶茶"  # first input flushed


def test_flush():
    fsm = DialogueFSM()
    fsm.debounce_input("hello")
    flushed = fsm.flush()
    assert flushed == "hello"
    assert fsm.flush() is None


def test_history_tracking():
    fsm = DialogueFSM()
    fsm.transition(State.INTENT_DETECTED, intent="search", reason="user_query")
    fsm.transition(State.SEARCHING, reason="clear_entities")

    history = fsm.get_history()
    assert len(history) == 2
    assert history[0]["from"] == "idle"
    assert history[0]["to"] == "intent_detected"
    assert history[1]["from"] == "intent_detected"
    assert history[1]["to"] == "searching"


def test_stable_states():
    """Verify which states are stable."""
    fsm = DialogueFSM()
    assert fsm.is_stable  # IDLE is stable

    fsm.transition(State.INTENT_DETECTED)
    assert not fsm.is_stable

    fsm.transition(State.SEARCHING)
    assert not fsm.is_stable

    fsm.transition(State.PRESENTING)
    assert fsm.is_stable

    fsm.transition(State.ORDERING)
    assert fsm.is_stable
