"""Tool Executor — unified entry point for Agent tool calls with error handling."""

from src.observability.logger import get_logger
from src.tools.registry import get_tool_by_name

logger = get_logger("tool_executor")


async def execute_tool(name: str, args: dict) -> dict:
    """Execute a tool by name with error handling and logging.

    Returns:
        {"success": True, "data": result} or {"success": False, "error": str}
    """
    tool = get_tool_by_name(name)
    if tool is None:
        logger.error("tool_not_found", tool=name)
        return {"success": False, "error": f"Tool '{name}' not found"}

    try:
        result = tool.func(**args)
        # Handle both sync and async functions
        import inspect
        if inspect.isawaitable(result):
            result = await result
        logger.info("tool_executed", tool=name, result_type=type(result).__name__)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error("tool_failed", tool=name, error=str(e))
        return {"success": False, "error": str(e)}
