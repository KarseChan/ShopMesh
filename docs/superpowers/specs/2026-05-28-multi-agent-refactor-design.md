# Multi-Agent 架构精简重构

## 背景

当前项目共存三套图架构（`shopping_graph.py`、`shopping_agent.py`、`multi_agent_graph.py`），其中多 Agent 架构存在以下问题：

1. **`order_agent` 不是 Agent** — 硬编码返回字符串，0 个工具，无 ReAct 循环
2. **`recommend_agent` 和 `search_agent` 差异极小** — 共享 `_run_agent_loop`，仅差 1 个工具（`multi_query_search`）和 system prompt
3. **Orchestrator DAG 的 LLM 分解是 ~95% 场景的死代码** — 单意图走确定性映射
4. **三套架构互相竞争** — CLI 用 pipeline，API 默认用 multi_agent，agent 模式又是一套

## 目标

采用**方案 C：精简为 2 个真正的 Agent**，具体变更：

### 1. 合并 Agent：5 → 2

| 新 Agent | 合并来源 | 工具集 | response_type |
|----------|----------|--------|---------------|
| `search_recommend_agent` | `recommend_agent` + `search_agent` | product_search, multi_query_search, ask_clarification, constraint_relaxation, review_summary | `recommendation_cards` |
| `detail_compare_agent` | `detail_agent` + `compare_agent` | product_search, product_detail_batch, price_compare, review_summary | `comparison_table` |

- `order_agent` 降级为后处理硬编码分支（不经过 ReAct 循环）
- 合并后的 prompt 需融合原有两个 prompt 的决策规则

### 2. 更新意图映射

```python
INTENT_TO_AGENT = {
    "recommend_product": "search_recommend_agent",
    "find_product": "search_recommend_agent",
    "compare_products": "detail_compare_agent",
    "view_detail": "detail_compare_agent",
    "place_order": "__order_hardcoded__",  # 特殊标记，走硬编码分支
}
```

### 3. 更新 config.yaml

```yaml
agents:
  search_recommend_agent:
    max_iterations: 5
    response_type: recommendation_cards
    tools: [product_search, multi_query_search, ask_clarification, constraint_relaxation, review_summary]
  detail_compare_agent:
    max_iterations: 6
    response_type: comparison_table
    tools: [product_search, product_detail_batch, price_compare, review_summary]
```

### 4. Graph 结构变更

- `multi_agent_graph.py` 的 legacy path：移除 `order_agent` 节点，`place_order` 意图在 `agent_router` 中直接走 postprocess
- 保留 Orchestrator DAG path 不变（它引用的是 task templates，不直接依赖 agent 名称）
- 更新 `task_templates.py` 中的 agent 引用

### 5. Prompt 合并策略

- `search_recommend_agent`：以 `recommend_prompt.py` 为基础，融入 `search_prompt.py` 的"纯搜索"模式判断（当用户意图是 `find_product` 时，不做推荐判断，返回 `product_grid`）
- `detail_compare_agent`：以 `compare_prompt.py` 为基础，融入 `detail_prompt.py` 的"单商品深挖"模式判断（当用户意图是 `view_detail` 时，走固定流程）

### 6. Response Schema

- `search_recommend_agent` 需支持两种 response_type：`recommendation_cards` 和 `product_grid`（根据意图动态选择）
- `detail_compare_agent` 需支持两种 response_type：`detail_card` 和 `comparison_table`
- 更新 `response_schemas.py` 的 `RESPONSE_SCHEMAS` 映射

### 7. 不改动的部分

- `shopping_graph.py` — 按 CLAUDE.md 规定不删除、不修改
- `shopping_agent.py` — 保留作为单 Agent 模式
- 预处理层（`preprocessing.py`）— 完全保留
- 后处理层（`postprocessing.py`）— 完全保留，order 逻辑在此处理
- 工具层（`src/tools/`）— 完全保留
- 记忆系统 — 完全保留

## 验收标准

1. API `mode="multi_agent"` 走新 2-Agent 架构，所有意图正常路由
2. `place_order` 意图返回硬编码确认信息，不经过 LLM
3. `recommend_product` 和 `find_product` 都走 `search_recommend_agent`，但输出格式根据意图自适应
4. `compare_products` 和 `view_detail` 都走 `detail_compare_agent`，但输出格式根据意图自适应
5. Orchestrator DAG path 正常工作
6. 原有测试通过
7. config.yaml 只有 2 个 agent 配置
