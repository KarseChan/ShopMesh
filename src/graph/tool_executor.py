"""Tool Executor — unified entry point for Agent tool calls with error handling.

P2-1: Integrated hooks system for pre/post tool execution events.
"""

from src.auth.context import get_context_user_id
from src.observability.logger import get_logger
from src.security.data_guard import sanitize_for_log
from src.security.permission import PermissionViolation, check_permission, check_rate_limit
from src.tools.registry import get_tool_by_name

logger = get_logger("tool_executor")

# Fields to extract from args per tool for logging (no PII)
_TOOL_ARGS_LOG_FIELDS = {
    "product_search": ["semantic_query"],
    "multi_query_search": ["search_requests"],
    "product_detail_batch": ["product_ids"],
    "price_compare": ["product_ids"],
    "review_summary": ["product_ids", "aspects"],
    "constraint_relaxation": ["failed_reason"],
    "ask_clarification": ["asked_fields", "search_failed"],
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
    "multi_query_search": lambda r: {
        "total": r.get("total", 0),
        "queries_executed": r.get("queries_executed", 0),
        "types": list(r.get("by_type", {}).keys()),
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
        "strategy": r.get("strategy", ""),
        "fields": r.get("fields", []),
        "question_count": r.get("question_count", 0),
        "question_type": r.get("question_type", ""),
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
            if k in ("category", "product_type", "gender", "brand", "scenario",
                     "preference", "price_min", "price_max",
                     "soft_requirements", "hard_constraints")
        }

    # Sanitize: mask any PII patterns in logged strings
    return sanitize_for_log(log_args)


def _extract_result_for_log(tool_name: str, result) -> dict:
    """Extract key fields from tool result for logging."""
    extractor = _TOOL_RESULT_LOG_FIELDS.get(tool_name)
    if extractor and isinstance(result, dict):
        try:
            return extractor(result)
        except (KeyError, TypeError, IndexError, AttributeError) as e:
            # Extractors index into the result dict/list; narrowed to those
            # shape-mismatch errors so a broken extractor is discoverable at
            # debug level while genuinely unexpected errors still propagate.
            logger.debug("result_log_extract_failed", tool=tool_name, error=str(e))
    return {"result_type": type(result).__name__}


async def execute_tool(name: str, args: dict) -> dict:
    """Execute a tool by name with error handling and logging.

    P2-1: Integrated hooks for pre/post tool execution events.

    Returns:
        {"success": True, "data": result} or {"success": False, "error": str}
    """
    from src.graph.hooks import trigger_hooks

    tool = get_tool_by_name(name)
    if tool is None:
        logger.error("tool_not_found", tool=name)
        return {"success": False, "error": f"Tool '{name}' not found"}

    # Permission check:
    # - READ: always allowed
    # - WRITE (如购物车增删改,可撤销): 允许 agent 直接调用
    # - SENSITIVE (下单/支付/退款): 在此拦截,必须走 HITL 确认子图,不能从普通工具循环执行
    from src.skills.schema import PermissionLevel
    try:
        check_permission(tool.permissions, user_confirmed=(tool.permissions == PermissionLevel.WRITE))
    except PermissionViolation as e:
        logger.warning("tool_permission_denied", tool=name, reason=str(e))
        return {"success": False, "error": str(e)}

    # Rate limit check
    user_id = get_context_user_id() or "anonymous"
    try:
        check_rate_limit(user_id)
    except PermissionViolation as e:
        logger.warning("tool_rate_limited", tool=name, user_id=user_id, reason=str(e))
        return {"success": False, "error": "操作过于频繁，请稍后再试"}

    # P2-1: Pre-tool-use hooks
    hook_result = await trigger_hooks("pre_tool_use", tool_name=name, args=args)
    if hook_result and hook_result.block:
        logger.info("tool_blocked_by_hook", tool=name, message=hook_result.message)
        return {"success": False, "error": hook_result.message}

    args_log = _extract_args_for_log(name, args)

    try:
        result = tool.func(**args)
        import inspect
        if inspect.isawaitable(result):
            result = await result

        # Extract _full_products: store in ResultStore, keep only slim results
        if isinstance(result, dict) and "_full_products" in result:
            full_products = result.pop("_full_products")
            if full_products:
                from src.retrieval.result_store import get_result_store
                store = get_result_store()
                result_id = store.store_products(full_products, tool_name=name)
                result["result_id"] = result_id

        result_log = _extract_result_for_log(name, result)
        logger.info("tool_executed", tool=name, args=args_log, result=result_log)

        # P2-1: Post-tool-use hooks
        await trigger_hooks("post_tool_use", tool_name=name, args=args, result=result)

        return {"success": True, "data": result}
    except Exception as e:
        logger.error("tool_failed", tool=name, args=args_log, error=str(e))
        return {"success": False, "error": str(e)}
