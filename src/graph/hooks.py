"""Hooks System — extensible hook mechanism for agent loop.

Four event points covering the agent cycle:
- pre_tool_use: Before tool execution (permission, validation, logging)
- post_tool_use: After tool execution (output check, side effects, logging)
- pre_llm_call: Before LLM call (context injection, input modification)
- post_llm_call: After LLM call (output check, statistics)

Usage:
    from src.graph.hooks import register_hook, trigger_hooks, HookResult

    def my_hook(tool_name: str, args: dict, **kwargs) -> HookResult | None:
        logger.info("tool_called", tool=tool_name)
        return None  # Don't block

    register_hook("pre_tool_use", my_hook)
"""

import inspect
from dataclasses import dataclass, field
from typing import Callable, Any

from src.observability.logger import get_logger

logger = get_logger("hooks")


@dataclass
class HookResult:
    """Hook return value. block=True prevents execution from continuing."""
    block: bool = False
    message: str = ""
    data: dict = field(default_factory=dict)


# Hook callback type: accepts keyword arguments, returns HookResult or None
HookCallback = Callable[..., HookResult | None | None]

# Registry: event name -> list of callbacks
_HOOKS: dict[str, list[HookCallback]] = {
    "pre_tool_use": [],
    "post_tool_use": [],
    "pre_llm_call": [],
    "post_llm_call": [],
}


def register_hook(event: str, callback: HookCallback) -> None:
    """Register a hook callback for an event.

    Args:
        event: One of pre_tool_use, post_tool_use, pre_llm_call, post_llm_call
        callback: Function that receives event-specific kwargs and returns HookResult or None

    Raises:
        ValueError: If event is not recognized
    """
    if event not in _HOOKS:
        raise ValueError(f"Unknown hook event: {event}. Must be one of: {list(_HOOKS.keys())}")
    _HOOKS[event].append(callback)
    logger.debug("hook_registered", event_name=event, callback=callback.__name__)


def unregister_hook(event: str, callback: HookCallback) -> bool:
    """Unregister a hook callback. Returns True if found and removed."""
    if event not in _HOOKS:
        return False
    try:
        _HOOKS[event].remove(callback)
        return True
    except ValueError:
        return False


def clear_hooks(event: str | None = None) -> None:
    """Clear all hooks for an event, or all events if None."""
    if event is None:
        for e in _HOOKS:
            _HOOKS[e] = []
    elif event in _HOOKS:
        _HOOKS[event] = []


def get_hook_count(event: str) -> int:
    """Get the number of registered hooks for an event."""
    return len(_HOOKS.get(event, []))


async def trigger_hooks(event: str, **kwargs) -> HookResult | None:
    """Trigger all hooks for an event. Returns first blocking result.

    Args:
        event: Hook event name
        **kwargs: Event-specific arguments passed to each hook

    Returns:
        HookResult with block=True if any hook blocked, None otherwise
    """
    if event not in _HOOKS:
        logger.warning("unknown_hook_event", event_name=event)
        return None

    for callback in _HOOKS[event]:
        try:
            result = callback(**kwargs)
            # Support async callbacks
            if inspect.isawaitable(result):
                result = await result

            if result is not None and result.block:
                logger.info("hook_blocked",
                             event_name=event,
                             callback=callback.__name__,
                             message=result.message)
                return result

        except Exception as e:
            # Hook errors don't block execution, just log warning
            logger.warning("hook_error",
                            event_name=event,
                            callback=callback.__name__,
                            error=str(e))

    return None
