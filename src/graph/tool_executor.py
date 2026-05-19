"""Tool Executor — unified entry point for Agent tool calls with error handling."""

from src.observability.logger import get_logger
from src.tools.registry import get_tool_by_name

logger = get_logger("tool_executor")

# Fields to extract from args per tool for logging (no PII)
_TOOL_ARGS_LOG_FIELDS = {
    "product_search": ["semantic_query"],
    "product_detail_batch": ["product_ids"],
    "price_compare": ["product_ids"],
    "review_summary": ["product_ids", "aspects"],
    "constraint_relaxation": ["failed_reason"],
    "ask_clarification": ["asked_fields"],
}

# Fields to extract from result for logging
_TOOL_RESULT_LOG_FIELDS = {
    "product_search": lambda r: {
        "total": r.get("total", 0),
        "exact": r.get("exact", 0),
        "supplemental": r.get("supplemental", 0),
        "exact_product_ids": r.get("exact_product_ids", [])[:5],
        "supplemental_product_ids": r.get("supplemental_product_ids", [])[:5],
    },
    "product_detail_batch": lambda r: {
        "count": len(r) if isinstance(r, list) else 0,
        "product_ids": [p.get("product_id", "") for p in (r[:5] if isinstance(r, list) else [])],
    },
    "price_compare": lambda r: {
        "product_ids": list(r.get("products", {}).keys())[:5] if isinstance(r, dict) else [],
    },
    "review_summary": lambda r: {
        "count": len(r) if isinstance(r, list) else 0,
        "product_ids": [p.get("product_id", "") for p in (r[:5] if isinstance(r, list) else [])],
    },
    "constraint_relaxation": lambda r: {
        "relaxed": r.get("relaxed", []),
        "steps_remaining": r.get("steps_remaining", 0),
    },
    "ask_clarification": lambda r: {
        "should_ask": r.get("should_ask", False),
        "question_count": len(r.get("questions", [])),
    },
}


def _extract_args_for_log(tool_name: str, args: dict) -> dict:
    """Extract key fields from tool args for logging, filtering out PII."""
    log_args = {}
    fields = _TOOL_ARGS_LOG_FIELDS.get(tool_name, [])
    for field in fields:
        if field in args:
            log_args[field] = args[field]

    # product_search: log entities summary (no user_id, no user_profile)
    if tool_name == "product_search" and "entities" in args:
        ents = args["entities"]
        log_args["entities"] = {
            k: v for k, v in ents.items()
            if k in ("category", "product_type", "brand", "scenario",
                     "preference", "price_min", "price_max",
                     "soft_requirements", "hard_constraints")
        }

    return log_args


def _extract_result_for_log(tool_name: str, result) -> dict:
    """Extract key fields from tool result for logging."""
    extractor = _TOOL_RESULT_LOG_FIELDS.get(tool_name)
    if extractor and isinstance(result, dict):
        try:
            return extractor(result)
        except Exception:
            pass
    return {"result_type": type(result).__name__}


async def execute_tool(name: str, args: dict) -> dict:
    """Execute a tool by name with error handling and logging.

    Returns:
        {"success": True, "data": result} or {"success": False, "error": str}
    """
    tool = get_tool_by_name(name)
    if tool is None:
        logger.error("tool_not_found", tool=name)
        return {"success": False, "error": f"Tool '{name}' not found"}

    args_log = _extract_args_for_log(name, args)

    try:
        result = tool.func(**args)
        import inspect
        if inspect.isawaitable(result):
            result = await result

        result_log = _extract_result_for_log(name, result)
        logger.info("tool_executed", tool=name, args=args_log, result=result_log)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error("tool_failed", tool=name, args=args_log, error=str(e))
        return {"success": False, "error": str(e)}
