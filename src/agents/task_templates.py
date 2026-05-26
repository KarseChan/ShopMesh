"""Task Templates — predefined whitelist for Orchestrator DAG decomposition.

Each template defines a task's type (tool or agent), the tool/agent it uses,
and default argument patterns. The Orchestrator can only combine these templates,
not invent arbitrary tasks.
"""

from dataclasses import dataclass, field


@dataclass
class TaskTemplate:
    """A predefined task template."""
    task_id: str
    type: str  # "tool" or "agent"
    tool: str | None = None       # for type="tool"
    agent: str | None = None      # for type="agent"
    default_args: dict = field(default_factory=dict)
    description: str = ""


# ──────────────────────────────────────────────
# Task Template Whitelist
# ──────────────────────────────────────────────

TASK_TEMPLATES: dict[str, TaskTemplate] = {
    "history_lookup": TaskTemplate(
        task_id="history_lookup",
        type="tool",
        tool="product_detail_batch",
        default_args={},
        description="查历史商品当前详情（需要 product_ids，通常来自 memory_chunks）",
    ),
    "market_search": TaskTemplate(
        task_id="market_search",
        type="tool",
        tool="product_search",
        default_args={},
        description="搜索当前市场的商品候选（需要 entities + semantic_query）",
    ),
    "price_check": TaskTemplate(
        task_id="price_check",
        type="tool",
        tool="price_compare",
        default_args={},
        description="多商品比价/降价查询（需要 product_ids，依赖 history_lookup）",
    ),
    "compare": TaskTemplate(
        task_id="compare",
        type="agent",
        agent="compare_agent",
        description="对比多个商品，输出对比表和结论",
    ),
    "recommend": TaskTemplate(
        task_id="recommend",
        type="agent",
        agent="recommend_agent",
        description="综合推荐，基于搜索结果、比价、对比等信息给出最终推荐",
    ),
    "detail": TaskTemplate(
        task_id="detail",
        type="agent",
        agent="detail_agent",
        description="查看单个商品的详细信息和评价",
    ),
    "search": TaskTemplate(
        task_id="search",
        type="agent",
        agent="search_agent",
        description="搜索商品，返回结果列表",
    ),
    "review": TaskTemplate(
        task_id="review",
        type="tool",
        tool="review_summary",
        default_args={},
        description="获取商品评论摘要（需要 product_ids）",
    ),
}

# ──────────────────────────────────────────────
# Intent → Task Mapping (deterministic shortcuts)
# ──────────────────────────────────────────────

INTENT_TO_TASKS: dict[str, list[str]] = {
    "place_order":       ["history_lookup", "price_check"],
    "compare_products":  ["compare"],
    "recommend_product": ["market_search", "recommend"],
    "find_product":      ["search"],
    "view_detail":       ["detail"],
}


def get_template(task_id: str) -> TaskTemplate | None:
    """Get a task template by ID. Returns None if not found."""
    return TASK_TEMPLATES.get(task_id)


def get_template_names() -> list[str]:
    """Return all available task template IDs."""
    return list(TASK_TEMPLATES.keys())


def resolve_intent_tasks(user_goals: list[str]) -> list[str] | None:
    """Resolve user goals to task template IDs deterministically.

    Returns task IDs if all goals have a deterministic mapping, None otherwise.
    For multi-goal, unions the task lists (deduplicated, order preserved).
    """
    if not user_goals:
        return None

    task_ids = []
    seen = set()
    all_mapped = True

    for goal in user_goals:
        mapped = INTENT_TO_TASKS.get(goal)
        if mapped is None:
            all_mapped = False
            break
        for tid in mapped:
            if tid not in seen:
                seen.add(tid)
                task_ids.append(tid)

    return task_ids if all_mapped else None
