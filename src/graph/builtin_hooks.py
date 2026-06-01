"""Built-in Hooks — default hook implementations for common use cases.

These hooks are registered automatically when imported.
"""

from src.graph.hooks import HookResult, register_hook
from src.observability.logger import get_logger

logger = get_logger("builtin_hooks")


def log_tool_call(tool_name: str, args: dict, **kwargs) -> HookResult | None:
    """Log every tool call before execution."""
    logger.info("hook_pre_tool", tool=tool_name)
    return None  # Don't block


def log_tool_result(tool_name: str, result: dict, **kwargs) -> HookResult | None:
    """Log tool execution result."""
    result_size = len(str(result))
    logger.info("hook_post_tool", tool=tool_name, result_size=result_size)

    # Warn on large outputs
    if result_size > 100_000:
        logger.warning("hook_large_output", tool=tool_name, size=result_size)

    return None


def log_llm_call(agent: str, **kwargs) -> HookResult | None:
    """Log LLM call start."""
    logger.info("hook_pre_llm", agent=agent)
    return None


def log_llm_response(agent: str, response: dict, **kwargs) -> HookResult | None:
    """Log LLM response summary."""
    content_len = len(response.get("content", ""))
    has_tools = bool(response.get("tool_calls"))
    logger.info("hook_post_llm", agent=agent, content_len=content_len, has_tools=has_tools)
    return None


def register_builtin_hooks() -> None:
    """Register all built-in hooks. Call once at startup."""
    register_hook("pre_tool_use", log_tool_call)
    register_hook("post_tool_use", log_tool_result)
    register_hook("pre_llm_call", log_llm_call)
    register_hook("post_llm_call", log_llm_response)
    logger.info("builtin_hooks_registered")
