# Orchestrator DAG 架构方案

> 从"单 Agent + 工具合并"演进为"Orchestrator 拆任务 DAG + 分阶段暴露工具"

## 背景

当前 multi_agent 架构采用 Strategy A：多意图时将多个 Agent 的工具集取并集，交给主 Agent 执行。

**现状数据**：
- 5 个 Agent，共 7 个工具，重叠度高
- 合并后主 Agent 拿到 6-7 个工具，尚在安全区
- 工具选择靠 prompt 里的确定性决策规则约束，LLM 自由选择空间小

**问题**：
1. 工具数量膨胀后（10+），LLM tool selection 准确率下降
2. 有数据依赖的复合意图（如"上次看的降价了吗 + 推荐更好的"），单 Agent 不一定能规划出正确的工具调用顺序
3. 所有任务串行执行，无法利用并行性降低延迟

## 架构设计

### 整体流程

```
用户输入
  │
  ▼
┌─────────────────────────────────────────────────┐
│  preprocess（确定性，并行）                       │
│  classify_intent → extract_entities → recall    │
│  session_memory → user_profile → search_plan    │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│  orchestrator（LLM 拆任务 DAG）                  │
│  输入: user_goals + entities + memory + tools    │
│  输出: task_dag (JSON)                           │
│  约束: 只能从预定义 task template 组合            │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│  dag_executor（DAG 执行引擎）                    │
│  拓扑排序 → 并行执行无依赖 task → 依赖传递       │
│  type:tool → 直接调用（无 LLM）                  │
│  type:agent → 启动 ReAct 循环（工具子集）        │
└──────────────────────┬──────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────┐
│  postprocess（确定性）                           │
│  偏好提取 + 记忆写入                             │
└──────────────────────┬──────────────────────────┘
                       ▼
                     END
```

### Orchestrator 节点

Orchestrator 是一个 LLM 节点，职责是将复合意图拆解为有向无环图（DAG）。

**输入**：
```python
{
    "user_goals": ["recommend_product", "place_order", "compare_products"],
    "entities": {
        "category": "数码",
        "product_type": "相机",
        "price_max": 5000,
        "reference": "上次看的那款",
        "compare_target": "索尼A7"
    },
    "memory_chunks": [{"product_id": "cam_003", "name": "佳能R50", "price": 4599}],
    "available_tools": ["product_search", "product_detail_batch", "price_compare",
                        "review_summary", "multi_query_search", "ask_clarification"]
}
```

**输出**（task DAG）：
```json
[
  {
    "task_id": "history_lookup",
    "type": "tool",
    "tool": "product_detail_batch",
    "args": {"product_ids": ["cam_003"]},
    "depends_on": [],
    "reason": "查上次看的那款当前详情"
  },
  {
    "task_id": "market_search",
    "type": "tool",
    "tool": "product_search",
    "args": {
      "entities": {"category": "数码", "product_type": "相机", "price_max": 5000},
      "semantic_query": "5000元相机推荐"
    },
    "depends_on": [],
    "reason": "搜索当前市场的相机候选"
  },
  {
    "task_id": "price_check",
    "type": "tool",
    "tool": "price_compare",
    "args": {"product_ids": ["cam_003"], "platforms": ["京东", "淘宝", "拼多多"]},
    "depends_on": ["history_lookup"],
    "reason": "降价查询需要先拿到商品详情"
  },
  {
    "task_id": "compare",
    "type": "agent",
    "agent": "compare_agent",
    "args": {"compare_targets": ["cam_003", "索尼A7"]},
    "depends_on": ["history_lookup", "market_search"],
    "reason": "对比需要知道 cam_003 详情和市场候选"
  },
  {
    "task_id": "recommend",
    "type": "agent",
    "agent": "recommend_agent",
    "depends_on": ["price_check", "compare", "market_search"],
    "reason": "最终推荐需要降价信息+对比结果+市场候选"
  }
]
```

**关键设计**：
- Orchestrator 只能从预定义的 task template 组合，不能自由发明 task
- 每个 task 必须指定 `type`（tool 或 agent）和 `depends_on`
- `type: tool` 的 task 由 Orchestrator 直接填 args，dag_executor 直接调用
- `type: agent` 的 task 只指定 agent 名，具体工具调用由 agent 的 ReAct 循环决定

### DAG 执行引擎

```python
async def execute_dag(dag: list[dict], state: dict) -> dict:
    """Execute task DAG with dependency-aware parallelism."""

    # 1. 拓扑排序
    sorted_tasks = topological_sort(dag)

    # 2. 按层级并行执行
    task_results = {}
    while sorted_tasks:
        # 找出所有依赖已满足的 task
        ready = [t for t in sorted_tasks
                 if all(dep in task_results for dep in t["depends_on"])]

        # 并行执行
        results = await asyncio.gather(*[
            _execute_task(task, task_results, state) for task in ready
        ])

        # 收集结果
        for task, result in zip(ready, results):
            task_results[task["task_id"]] = result

        # 移除已执行的 task
        sorted_tasks = [t for t in sorted_tasks if t not in ready]

    return task_results


async def _execute_task(task: dict, prior_results: dict, state: dict) -> dict:
    """Execute a single task: tool call or agent ReAct loop."""

    # 注入依赖 task 的结果到 args
    args = _inject_dependencies(task.get("args", {}), task["depends_on"], prior_results)

    if task["type"] == "tool":
        # 直接调用，不需要 LLM
        return await execute_tool(task["tool"], args)

    elif task["type"] == "agent":
        # 启动 ReAct 循环，只暴露该 agent 的工具子集
        agent_name = task["agent"]
        cfg = get_agent_config(agent_name)
        tool_schemas = get_tool_schemas_for_agent(agent_name)

        # 将 prior_results 注入 agent 的上下文
        agent_state = {**state, "_prior_task_results": prior_results}
        return await _run_agent_loop(agent_state, agent_name)
```

### 执行时间轴示例

```
用户: "我想买个相机，预算 5000，上次看的那款降价了吗？顺便和索尼 A7 对比一下"

t=0s   ┌─ history_lookup (product_detail_batch) ─── cam_003 详情     [2s]
       │
       └─ market_search (product_search) ─── 5000元相机候选列表      [2s]

t=2s   history_lookup 完成 → 触发 price_check (price_compare)        [2s]
       market_search 完成 → 等待 history_lookup

t=4s   history_lookup + market_search 都完成 → 触发 compare_agent    [3s]
       price_check 完成 → 等待 compare

t=7s   compare 完成 + price_check 完成 → 触发 recommend_agent        [3s]

t=10s  recommend 完成 → postprocess → 返回用户

总延迟: ~10s（并行）
对比串行: ~12s（5 个 task 串行）
对比当前方案: ~6s（单 Agent 2-3 轮 ReAct，但无法保证正确处理复合依赖）
```

### 工具隔离

每个 task 只拿到自己需要的工具：

```
task: history_lookup
  type: tool
  工具: [product_detail_batch]                              ← 1 个，直接调用
  无 LLM

task: market_search
  type: tool
  工具: [product_search]                                    ← 1 个，直接调用
  无 LLM

task: price_check
  type: tool
  工具: [price_compare]                                     ← 1 个，直接调用
  无 LLM

task: compare
  type: agent
  工具: [product_detail_batch, price_compare, review_summary]  ← 3 个
  ReAct 循环，LLM 决策

task: recommend
  type: agent
  工具: [product_search, review_summary, ask_clarification]    ← 3 个
  ReAct 循环，LLM 决策
```

### Orchestrator 的约束机制

Orchestrator 是 LLM 节点，可能拆错任务。约束手段：

1. **Task Template 白名单**：Orchestrator 只能从预定义的 template 组合，不能自由发明 task

```python
TASK_TEMPLATES = {
    "history_lookup": {"type": "tool", "tool": "product_detail_batch"},
    "market_search":  {"type": "tool", "tool": "product_search"},
    "price_check":    {"type": "tool", "tool": "price_compare"},
    "compare":        {"type": "agent", "agent": "compare_agent"},
    "recommend":      {"type": "agent", "agent": "recommend_agent"},
    "detail":         {"type": "agent", "agent": "detail_agent"},
    "search":         {"type": "agent", "agent": "search_agent"},
}
```

2. **依赖规则约束**：禁止循环依赖，限制 DAG 深度（最多 3 层）

3. **意图→Task 映射规则**：部分映射是确定性的，不经过 LLM

```python
INTENT_TO_TASKS = {
    "place_order":      ["history_lookup", "price_check"],
    "compare_products": ["compare"],
    "recommend_product": ["market_search", "recommend"],
}
```

4. **Fallback**：Orchestrator 输出解析失败时，降级为当前的单 Agent + 合并工具方案

## 与当前架构的对比

| 维度 | 当前方案 (Strategy A) | Orchestrator DAG |
|------|----------------------|------------------|
| 任务拆解 | 不拆，单 Agent 硬跑 | LLM 拆 DAG |
| 工具暴露 | 合并后全量给主 Agent (6-7 个) | 每个 task 只拿自己的工具 (1-3 个) |
| 并行执行 | 无 | 无依赖的 task 并行 |
| type:tool | 不存在 | 直接调用，不走 LLM |
| 延迟 | 单 Agent 2-3 轮 ReAct (~6s) | DAG 并行，关键路径 (~10s) |
| 可靠性 | 高（单 Agent，简单） | 中（Orchestrator 可能拆错） |
| 复杂度 | 低（现有 LangGraph） | 高（DAG 执行引擎 + 错误处理） |
| 适用场景 | 简单复合意图 | 有数据依赖的复杂复合意图 |

## 实现计划

### 需要新增的模块

| 模块 | 文件 | 职责 | 工作量 |
|------|------|------|--------|
| Orchestrator | `src/graph/orchestrator.py` | LLM 拆任务 DAG，prompt + JSON 解析 + 校验 | ~120 行 |
| DAG 执行引擎 | `src/graph/dag_executor.py` | 拓扑排序 + 并行执行 + 依赖传递 + 错误处理 | ~150 行 |
| Task Templates | `src/agents/task_templates.py` | 预定义 task template 白名单 + 意图→task 映射 | ~60 行 |

### 需要修改的模块

| 模块 | 改什么 | 工作量 |
|------|--------|--------|
| `agent_state.py` | 新增 `task_dag`, `task_results`, `task_status` 字段 | ~10 行 |
| `multi_agent_graph.py` | 图结构改为 preprocess → orchestrator → dag_executor → postprocess | ~40 行 |
| `specialized_agents.py` | agent 节点支持接收外部注入的工具子集 | ~20 行 |
| SSE 层 | 支持按 task 粒度推送进度事件 | ~30 行 |

### 总工作量

约 430 行新代码 + 100 行修改。核心难点在 `dag_executor` 的并行执行、错误传播、超时降级。

### 分阶段实施

**Phase 1（MVP）**：Orchestrator 只做 task 拆解，dag_executor 串行执行（不并行）
- 验证 Orchestrator 的拆解质量
- 不引入并行复杂度
- 工作量 ~200 行

**Phase 2**：dag_executor 支持并行执行
- 加入 asyncio.gather 并行
- 依赖传递和结果汇聚
- 工作量 ~150 行

**Phase 3**：错误处理和降级
- 单 task 超时降级（跳过该 task，用已有结果拼接）
- Orchestrator 拆解失败降级（回退到 Strategy A）
- 工作量 ~80 行
