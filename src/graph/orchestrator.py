"""Orchestrator — LLM node that decomposes compound intents into a task DAG.

The Orchestrator receives preprocessed state (user_goals, entities, memory)
and outputs a list of tasks with dependencies (a DAG). It can only combine
predefined task templates, not invent arbitrary tasks.

Fallback: if LLM decomposition fails, falls back to deterministic intent→task mapping.
"""

import json
import re

from src.agents.task_templates import (
    TASK_TEMPLATES,
    get_template,
    get_template_names,
    resolve_intent_tasks,
)
from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("orchestrator")

# Max DAG depth to prevent runaway chains
_MAX_DAG_DEPTH = 3
# Max tasks in a DAG
_MAX_TASKS = 8


# ──────────────────────────────────────────────
# Prompt
# ──────────────────────────────────────────────

def _build_orchestrator_prompt(
    user_goals: list[str],
    entities: dict,
    memory_chunks: list,
    search_plan: dict,
    available_templates: list[str],
) -> str:
    """Build the system prompt for the Orchestrator LLM."""

    template_descriptions = []
    for name in available_templates:
        tmpl = TASK_TEMPLATES[name]
        dep_hint = ""
        if name == "price_check":
            dep_hint = "（通常依赖 history_lookup）"
        elif name == "compare":
            dep_hint = "（依赖需要对比的商品信息）"
        elif name == "recommend":
            dep_hint = "（通常依赖其他任务的结果）"
        template_descriptions.append(f'  - "{name}": {tmpl.description}{dep_hint}')

    templates_text = "\n".join(template_descriptions)

    # Format memory info
    memory_text = "无"
    if memory_chunks:
        mem_items = []
        for m in memory_chunks[:5]:
            pid = m.get("product_id", "")
            name = m.get("name", "")
            price = m.get("price", "")
            mem_items.append(f'  - {pid}: {name} (¥{price})')
        memory_text = "\n".join(mem_items)

    # Format entities
    entity_items = []
    for k, v in entities.items():
        if v and not k.startswith("_") and k not in ("ambiguous", "ambiguous_fields", "missing_critical_fields"):
            entity_items.append(f'  - {k}: {v}')
    entities_text = "\n".join(entity_items) if entity_items else "无"

    return f"""你是一个任务规划器。根据用户的复合意图，将任务拆解为有向无环图（DAG）。

## 可用任务模板（只能从以下组合，不能发明新任务）
{templates_text}

## 当前上下文
用户意图: {user_goals}
提取的实体:
{entities_text}
记忆中的商品:
{memory_text}

## 输出格式
输出 JSON 数组，每个元素是一个 task:
```json
[
  {{
    "task_id": "模板名（必须是上面列出的模板之一）",
    "depends_on": ["依赖的 task_id 列表"],
    "args": {{}}
  }}
]
```

## 规则
1. task_id 必须是上面列出的模板名之一
2. depends_on 中引用的 task_id 必须在同一数组中存在
3. 禁止循环依赖
4. type:tool 的 task 需要在 args 中填入必要参数
5. type:agent 的 task 不需要填 args（由 agent 自行决定）
6. 如果只有一个简单意图，直接返回一个无依赖的 task 即可
7. args 中的 product_ids 如果来自记忆，用 "memory" 标记，执行时会自动替换

## 示例
用户说"上次看的那款降价了吗 + 推荐更好的":
```json
[
  {{"task_id": "history_lookup", "depends_on": [], "args": {{"product_ids": "memory"}}}},
  {{"task_id": "market_search", "depends_on": [], "args": {{"semantic_query": "推荐相机", "entities": "state"}}}},
  {{"task_id": "price_check", "depends_on": ["history_lookup"], "args": {{}}}},
  {{"task_id": "recommend", "depends_on": ["price_check", "market_search"], "args": {{}}}}
]
```

只输出 JSON 数组，不要输出其他内容。"""


# ──────────────────────────────────────────────
# Parsing & Validation
# ──────────────────────────────────────────────

def _parse_task_dag(raw: str) -> list[dict] | None:
    """Parse JSON array from LLM output. Returns None on failure."""
    text = raw.strip()

    # Strip markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # Find first JSON array
    if not text.startswith("["):
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            text = match.group(0)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    return None


def _validate_dag(dag: list[dict]) -> tuple[bool, str]:
    """Validate the task DAG structure.

    Returns (is_valid, error_message).
    """
    if not dag:
        return False, "空 DAG"

    if len(dag) > _MAX_TASKS:
        return False, f"任务数量超限: {len(dag)} > {_MAX_TASKS}"

    task_ids = set()
    for task in dag:
        tid = task.get("task_id", "")
        if not tid:
            return False, "缺少 task_id"
        if tid not in TASK_TEMPLATES:
            return False, f"未知 task_id: {tid}（不在白名单中）"
        if tid in task_ids:
            return False, f"重复 task_id: {tid}"
        task_ids.add(tid)

    # Check dependencies exist
    for task in dag:
        for dep in task.get("depends_on", []):
            if dep not in task_ids:
                return False, f"依赖不存在的 task: {dep}"

    # Check for cycles (simple DFS)
    if _has_cycle(dag):
        return False, "检测到循环依赖"

    # Check depth
    depth = _compute_depth(dag)
    if depth > _MAX_DAG_DEPTH:
        return False, f"DAG 深度超限: {depth} > {_MAX_DAG_DEPTH}"

    return True, ""


def _has_cycle(dag: list[dict]) -> bool:
    """Check if DAG has cycles using DFS."""
    adj: dict[str, list[str]] = {}
    for task in dag:
        tid = task.get("task_id", "")
        adj[tid] = task.get("depends_on", [])

    visited = set()
    in_stack = set()

    def dfs(node: str) -> bool:
        if node in in_stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in adj.get(node, []):
            if dfs(dep):
                return True
        in_stack.discard(node)
        return False

    return any(dfs(tid) for tid in adj)


def _compute_depth(dag: list[dict]) -> int:
    """Compute the max depth of the DAG."""
    adj: dict[str, list[str]] = {}
    for task in dag:
        tid = task.get("task_id", "")
        adj[tid] = task.get("depends_on", [])

    depth_cache: dict[str, int] = {}

    def get_depth(node: str) -> int:
        if node in depth_cache:
            return depth_cache[node]
        deps = adj.get(node, [])
        if not deps:
            depth_cache[node] = 0
            return 0
        d = 1 + max(get_depth(dep) for dep in deps)
        depth_cache[node] = d
        return d

    return max(get_depth(tid) for tid in adj) if adj else 0


# ──────────────────────────────────────────────
# Args Resolution
# ──────────────────────────────────────────────

def _resolve_task_args(task: dict, state: dict) -> dict:
    """Resolve placeholder values in task args.

    - "memory" → product_ids from memory_chunks
    - "state" → entities from state
    - Missing required args → fill from state context
    """
    args = dict(task.get("args", {}))
    tid = task.get("task_id", "")
    tmpl = get_template(tid)
    if not tmpl:
        return args

    # Resolve product_ids from memory
    if "product_ids" in args and args["product_ids"] == "memory":
        memory_chunks = state.get("memory_chunks", [])
        args["product_ids"] = [
            m.get("product_id", "") for m in memory_chunks if m.get("product_id")
        ]

    # Resolve entities from state
    if "entities" in args and args["entities"] == "state":
        args["entities"] = state.get("entities", {})

    # Fill missing args based on template type
    if tmpl.tool == "product_search":
        if "entities" not in args:
            args["entities"] = state.get("entities", {})
        if "semantic_query" not in args:
            # Build from entities
            entities = state.get("entities", {})
            parts = []
            if entities.get("category"):
                parts.append(entities["category"])
            if entities.get("product_type"):
                parts.append(entities["product_type"])
            if entities.get("scenario"):
                parts.append(entities["scenario"])
            args["semantic_query"] = "".join(parts) or "推荐商品"

    elif tmpl.tool == "price_compare":
        if "product_ids" not in args:
            memory_chunks = state.get("memory_chunks", [])
            args["product_ids"] = [
                m.get("product_id", "") for m in memory_chunks if m.get("product_id")
            ]

    elif tmpl.tool == "product_detail_batch":
        if "product_ids" not in args:
            memory_chunks = state.get("memory_chunks", [])
            args["product_ids"] = [
                m.get("product_id", "") for m in memory_chunks if m.get("product_id")
            ]

    return args


# ──────────────────────────────────────────────
# Orchestrator Node
# ──────────────────────────────────────────────

async def node_orchestrator(state: dict) -> dict:
    """Orchestrator node: decompose compound intent into task DAG.

    Strategy:
    1. Try deterministic mapping first (INTENT_TO_TASKS)
    2. If not all goals map deterministically, use LLM to decompose
    3. Validate DAG, fallback to single-agent if invalid

    Returns dict to merge into AgentState:
        task_dag: list[dict] — the task DAG
    """
    user_goals = state.get("user_goals", [])
    entities = state.get("entities", {})
    memory_chunks = state.get("memory_chunks", [])
    search_plan = state.get("search_plan", {})

    if not user_goals:
        user_goals = ["recommend_product"]

    # Step 1: Try deterministic mapping
    deterministic_tasks = resolve_intent_tasks(user_goals)
    if deterministic_tasks is not None:
        # Build DAG from deterministic mapping
        dag = _build_deterministic_dag(deterministic_tasks, state)
        logger.info("orchestrator_deterministic",
                     user_goals=user_goals,
                     task_count=len(dag))
        return {"task_dag": dag}

    # Step 2: LLM decomposition
    logger.info("orchestrator_llm_decompose", user_goals=user_goals)
    try:
        templates = get_template_names()
        prompt = _build_orchestrator_prompt(
            user_goals, entities, memory_chunks, search_plan, templates
        )

        llm = get_llm("orchestrator")
        response = await llm.chat([
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"用户意图: {user_goals}"},
        ])
        content = response.get("content", "")

        dag = _parse_task_dag(content)
        if dag is None:
            logger.warning("orchestrator_parse_failed", content=content[:300])
            return {"task_dag": _fallback_dag(user_goals, state)}

        # Resolve args placeholders
        for task in dag:
            task["args"] = _resolve_task_args(task, state)

        # Validate
        is_valid, error = _validate_dag(dag)
        if not is_valid:
            logger.warning("orchestrator_invalid_dag", error=error)
            return {"task_dag": _fallback_dag(user_goals, state)}

        logger.info("orchestrator_llm_success",
                     task_count=len(dag),
                     tasks=[t["task_id"] for t in dag])
        return {"task_dag": dag}

    except Exception as e:
        logger.error("orchestrator_error", error=str(e))
        return {"task_dag": _fallback_dag(user_goals, state)}


def _build_deterministic_dag(task_ids: list[str], state: dict) -> list[dict]:
    """Build a DAG from deterministic task IDs with known dependency patterns."""
    dag = []

    # Known dependency patterns
    deps: dict[str, list[str]] = {
        "price_check": ["history_lookup"],
        "recommend": [],  # will be filled below
        "compare": [],
    }

    # For recommend, depend on all other tasks except itself
    other_tasks = [t for t in task_ids if t != "recommend"]
    if "recommend" in task_ids:
        deps["recommend"] = other_tasks

    for tid in task_ids:
        task = {
            "task_id": tid,
            "depends_on": deps.get(tid, []),
            "args": {},
        }
        # Fill args for tool tasks
        task["args"] = _resolve_task_args(task, state)
        dag.append(task)

    return dag


def _fallback_dag(user_goals: list[str], state: dict) -> list[dict]:
    """Fallback: use the first goal's primary agent as a single task."""
    from src.agents.agent_config import resolve_agents

    agent_names = resolve_agents(user_goals)
    primary = agent_names[0] if agent_names else "recommend_agent"

    # Map agent name to task template
    agent_to_task = {
        "recommend_agent": "recommend",
        "search_agent": "search",
        "detail_agent": "detail",
        "compare_agent": "compare",
        "order_agent": "recommend",  # fallback
    }
    task_id = agent_to_task.get(primary, "recommend")

    logger.info("orchestrator_fallback", task_id=task_id, agent=primary)
    return [{
        "task_id": task_id,
        "depends_on": [],
        "args": {},
    }]
