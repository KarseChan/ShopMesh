# Multi-Agent Architecture Modification Plan

## Context

当前 ShoppingAgent 使用单一 ReAct 循环处理所有意图（search/recommend/compare/detail/order），存在以下问题：

- 一个通用 system prompt 塞了所有意图的决策规则，LLM 推理不聚焦
- 每次都把全部 7 个 tool schema 传给 LLM，大部分场景用不到
- 比价/详情等简单场景不需要 ReAct 多轮循环
- 所有意图输出相同格式，前端无法按场景区分渲染

改造目标：按意图分发到专用 Agent，每个 Agent 有自己的 prompt + tool 子集 + 迭代上限 + 输出格式。

## New Graph Topology

```
用户输入 → preprocess（含 clarification 路由）→ agent_router ─┬→ recommend_agent ─┬→ postprocess → END
                                               │               ├→ search_agent    ─┤
                                               │               ├→ detail_agent    ─┤
                                               │               ├→ compare_agent   ─┤
                                               │               └→ order_agent     ─┘
                                               │                                      ↓ fallback → postprocess → END
                                               │
                                               ├─ pending_clarification 存在？
                                               │   是 → clarification 路由（answer/task_switch/unclear）
                                               │   否 → 正常 intent/entity 提取
                                               └─ agent_router（确定性路由，纯 if/else，不用 LLM）

每个 agent 内部：ReAct loop（continue → 同一 agent / end → postprocess / fallback → fallback）
```

### 关键设计约束

1. **agent_router 是确定性路由**：基于 `intent["user_goal"]` 的 if/else 映射，不再调 LLM。
2. **pending_clarification 在 preprocess 内处理**：进入 agent_router 之前，clarification 状态机已经完成路由（answer/task_switch/unclear），agent 拿到的是已合并的 entities。
3. **JSON 输出有 schema 校验**：每个 Agent 的 Final Answer 用 Pydantic model 校验，解析失败时进入一次 repair 循环。

## 四类 Agent 分工

### A. Recommendation Agent — 帮用户做选择

**典型 query：**
- "我想买个送朋友的礼物，预算 200 左右"
- "我下周面试，想买正式但不老气的衣服"
- "推荐一款适合油皮的面霜"

**核心特征：** 不是"找商品"，而是"帮用户做选择"。需要动态决策能力。

**决策链路（Agent 自主决定每一步）：**
1. 是否追问？（ask_clarification → strategy=ask/light_ask/assume）
2. 搜索策略选择：product_search 还是 multi_query_search？
   - 品类明确 → product_search
   - 品类模糊 + 场景（如送礼/面试）→ multi_query_search 多路探索
3. 结果是否足够？不够 → constraint_relaxation → 重新搜索
4. 是否需要看口碑？→ review_summary
5. 最终主推哪个，备选哪个，理由是什么？

**示例："送朋友礼物"的 Agent 决策过程：**
```
Thought: 用户无品类，但有送礼场景和预算200。不强制追问，采用多路探索。
Action: multi_query_search(search_requests=[护肤礼盒, 数码小物, 运动配件], entities={price_max:200, scenario:送礼})
Observation: 返回12个商品
Thought: 结果充足，查看前3个口碑
Action: review_summary(product_ids=[P001, P002, P003])
Observation: P001好评如潮，P002口碑不错，P003库存少
Final Answer: 主推P001，备选P002，说明理由
```

**输出格式：** `response_type: "recommendation_cards"`
```json
{
  "response_type": "recommendation_cards",
  "recommendations": [
    {"product_id": "P001", "text": "推荐理由（结合场景+偏好+口碑）", "rank": 1},
    {"product_id": "P002", "text": "备选理由", "rank": 2}
  ],
  "summary": "综合你的需求，前两款最推荐"
}
```

**Tools：** product_search, multi_query_search, ask_clarification, constraint_relaxation, review_summary

**max_iterations：** 5

---

### B. Search Agent — 列出匹配商品

**典型 query：**
- "有哪些 200 左右的双肩包？"
- "帮我找男士衬衫"
- "搜一下小米背包"

**核心特征：** 目标是"列出匹配商品"，不是做推荐。用户已经知道自己要什么。

**决策链路（简单直接）：**
1. 实体完整？不完整 → ask_clarification（但很少需要，search 场景实体通常明确）
2. 调 product_search
3. 结果太少？→ constraint_relaxation → 重新搜索
4. 输出商品列表，不强行说"我最推荐"

**输出格式：** `response_type: "product_grid"`
```json
{
  "response_type": "product_grid",
  "products": [
    {"product_id": "P001", "match_type": "exact"},
    {"product_id": "P002", "match_type": "exact"},
    {"product_id": "P003", "match_type": "supplemental"}
  ],
  "total": 8,
  "summary": "找到8个匹配商品，按相关度排序"
}
```

**Tools：** product_search, constraint_relaxation, ask_clarification

**max_iterations：** 4

---

### C. Detail Agent — 解释某个商品

**典型 query：**
- "小米这个双肩包怎么样？"
- "这个商品有什么特点？"
- "这个包适合通勤吗？"

**核心特征：** 目标是"解释某个商品"。用户问的是特定商品，不是要搜索。

**决策链路：**
1. 先用 product_search 定位商品（按商品名/品牌搜索）
2. 再用 product_detail_batch 查详情
3. 再用 review_summary 看口碑
4. 输出 detail_card（适合"这个商品适合XX场景吗"的判断型问题）

**输出格式：** `response_type: "detail_card"`
```json
{
  "response_type": "detail_card",
  "product_id": "P001",
  "detail": {"name": "...", "price": 199, "rating": 4.7, "features": [...]},
  "review": {"reputation_label": "好评如潮", "selling_points": [...], "concerns": [...]},
  "verdict": "适合通勤，容量大且防水",
  "summary": "这款包综合评价很好，适合日常通勤使用"
}
```

**Tools：** product_search, product_detail_batch, review_summary

**max_iterations：** 3

---

### D. Compare Agent — 结构化比较并给建议

**典型 query：**
- "国家地理双肩包和小米双肩包哪个好？"
- "这两个衬衫怎么选？"
- "帮我对比一下 A 和 B"

**核心特征：** 目标是"结构化比较并给选择建议"。

**决策链路：**
1. 用 product_search 定位商品（按商品名搜索）
2. 用 product_detail_batch 获取所有商品详情
3. 用 price_compare 做结构化对比
4. 用 review_summary 看各自口碑
5. 输出对比表格 + 结论

**输出格式：** `response_type: "comparison_table"`
```json
{
  "response_type": "comparison_table",
  "products": [
    {"product_id": "P001", "price": 199, "rating": 4.7, "selling_points": [...], "concerns": [...]},
    {"product_id": "P002", "price": 249, "rating": 4.5, "selling_points": [...], "concerns": [...]}
  ],
  "best_value": "P001",
  "verdict": "追求性价比选P001，追求品质选P002",
  "summary": "两款各有优势，建议根据预算和使用场景选择"
}
```

**Tools：** product_search, product_detail_batch, price_compare, review_summary

**max_iterations：** 3

---

## Tool Assignment

| Agent | Tools | max_iterations | response_type |
|-------|-------|---------------|---------------|
| recommend_agent | product_search, multi_query_search, ask_clarification, constraint_relaxation, review_summary | 5 | recommendation_cards |
| search_agent | product_search, constraint_relaxation, ask_clarification | 4 | product_grid |
| detail_agent | product_search, product_detail_batch, review_summary | 3 | detail_card |
| compare_agent | product_search, product_detail_batch, price_compare, review_summary | 3 | comparison_table |
| order_agent | (无 tool) | 1 | order_confirmation |

**关键：** detail_agent 和 compare_agent 都需要 `product_search` 来定位商品（用户说"小米双肩包"时需要先搜索到对应 product_id）。

## Intent → Agent Mapping

| user_goal (from classifier) | Agent |
|---|---|
| recommend_product | recommend_agent |
| find_product | search_agent |
| compare_products | compare_agent |
| view_detail | detail_agent |
| place_order | order_agent |
| 其他/默认 | recommend_agent |

config.yaml `router.routes` 已有此映射，将被实际消费。

## Response Schema Validation

每个 Agent 的 Final Answer 必须通过 Pydantic schema 校验，不依赖 prompt 约束 alone。

### 8. `src/agents/response_schemas.py`（新增）

```python
class RecommendationItem(BaseModel):
    product_id: str
    text: str
    rank: int

class RecommendationResponse(BaseModel):
    response_type: Literal["recommendation_cards"]
    recommendations: list[RecommendationItem]
    summary: str

class ProductGridItem(BaseModel):
    product_id: str
    match_type: Literal["exact", "supplemental"]

class ProductGridResponse(BaseModel):
    response_type: Literal["product_grid"]
    products: list[ProductGridItem]
    total: int
    summary: str

class DetailCardResponse(BaseModel):
    response_type: Literal["detail_card"]
    product_id: str
    detail: dict
    review: dict
    verdict: str
    summary: str

class ComparisonTableItem(BaseModel):
    product_id: str
    price: float
    rating: float
    selling_points: list[str]
    concerns: list[str]

class ComparisonTableResponse(BaseModel):
    response_type: Literal["comparison_table"]
    products: list[ComparisonTableItem]
    best_value: str
    verdict: str
    summary: str
```

**Repair 机制：** Agent 返回 Final Answer 后，先尝试 JSON parse + schema 校验。如果失败：
1. 把原始输出 + schema 期望格式发回 LLM，要求修正
2. 重新 parse + 校验
3. 仍然失败 → 进入 fallback

这比纯 prompt 约束稳定得多。

## Files to Create (8 new)

### 1. `src/agents/agent_config.py`
- `AgentConfig` dataclass: name, tools, prompt_builder, max_iterations, response_type
- `AGENT_CONFIGS: dict[str, AgentConfig]` — 从 config.yaml 读取
- `INTENT_TO_AGENT: dict[str, str]` — user_goal → agent_name 映射
- `get_agent_config(name)`, `get_tools_for_agent(name)`, `get_tool_schemas_for_agent(name)`

### 2. `src/agents/prompts/__init__.py`
- 空 init

### 3. `src/agents/prompts/recommend_prompt.py`
- 从当前 `react_prompt.py` 的 `_SYSTEM_TEMPLATE` 演化
- 角色定义：你是推荐顾问，帮用户做选择
- 决策规则：ask_clarification → multi_query_search / product_search → constraint_relaxation → review_summary → Final Answer
- 强调：主推+备选的推荐格式，个性化理由
- 输出格式：recommendation_cards JSON

### 4. `src/agents/prompts/search_prompt.py`
- 角色定义：你是搜索助手，帮用户找到匹配商品
- 精简 prompt，无 multi_query 规则
- 决策规则：ask_clarification → product_search → constraint_relaxation → Final Answer
- 强调：列出商品，不做推荐判断
- 输出格式：product_grid JSON

### 5. `src/agents/prompts/detail_prompt.py`
- 角色定义：你是商品分析师，帮用户了解商品
- 决策规则：product_search（定位）→ product_detail_batch → review_summary → Final Answer
- 强调：客观分析商品特点，回答用户的场景问题（如"适合通勤吗"）
- 输出格式：detail_card JSON

### 6. `src/agents/prompts/compare_prompt.py`
- 角色定义：你是对比顾问，帮用户做选择
- 决策规则：product_search（定位）→ product_detail_batch → price_compare → review_summary → Final Answer
- 强调：结构化对比 + 明确结论（"追求性价比选A，追求品质选B"）
- 输出格式：comparison_table JSON

### 7. `src/graph/specialized_agents.py`
- `node_recommend_agent`, `node_search_agent`, `node_detail_agent`, `node_compare_agent`, `node_order_agent`
- 共享 `_run_agent_loop(state, agent_name)` 函数：
  1. 从 agent_config 拿 config + tool subset + prompt + response_type
  2. 组装 messages（复用 react_prompt 的格式函数）
  3. 调 LLM with filtered tool schemas
  4. 执行 tool / 解析 final answer
  5. 返回与当前 react_node 相同的 state dict 结构 + response_type
- 复用 `react_node.py` 的 `_inject_entity_fields` 逻辑
- 每个 agent node 使用自己的 should_continue（读 state 中的 max_iterations）

## Files to Modify (4 modified)

### 8. `src/graph/agent_state.py`
- 新增字段：`active_agent: str`
- 新增字段：`response_type: str`  — 告诉前端用什么渲染风格

### 9. `src/tools/registry.py`
- 新增：`get_tools_by_names(names: list[str]) -> list[ToolDef]`
- 新增：`get_tool_schemas_by_names(names: list[str]) -> list[dict]`

### 10. `config.yaml`
- 新增 `agents` section：
```yaml
agents:
  recommend_agent:
    max_iterations: 5
    response_type: recommendation_cards
    tools: [product_search, multi_query_search, ask_clarification, constraint_relaxation, review_summary]
  search_agent:
    max_iterations: 4
    response_type: product_grid
    tools: [product_search, constraint_relaxation, ask_clarification]
  detail_agent:
    max_iterations: 3
    response_type: detail_card
    tools: [product_search, product_detail_batch, review_summary]
  compare_agent:
    max_iterations: 3
    response_type: comparison_table
    tools: [product_search, product_detail_batch, price_compare, review_summary]
  order_agent:
    max_iterations: 1
    response_type: order_confirmation
    tools: []
```

### 11. `src/api/chat.py`
- mode 新增 `"multi_agent"` 选项，调用新的 `run_multi_agent_stream()`
- 默认 `"agent"` 不变，向后兼容
- SSE event 中 `results` 事件新增 `response_type` 字段

## Files NOT Modified (backward compat)

- `src/graph/shopping_agent.py` — 原 agent graph 完全不动
- `src/graph/react_node.py` — 原 react_node 不动
- `src/agents/react_prompt.py` — 原 prompt 不动，新 prompt 从其导入格式函数
- `src/graph/shopping_graph.py` — 原 DAG graph 不动
- 前端 — SSE event 格式向后兼容，新增 response_type 字段

## Implementation Order

**Phase 1 — Foundation（不影响现有功能）**
1. 创建 `src/agents/agent_config.py`
2. 创建 `src/agents/response_schemas.py`（Pydantic schema + parse/validate/repair 逻辑）
3. 创建 `src/agents/prompts/` 目录 + 4 个 prompt 文件
4. 修改 `src/tools/registry.py` 添加按名称过滤函数
5. 修改 `config.yaml` 添加 agents section

**Phase 2 — Agent Nodes**
6. 修改 `src/graph/agent_state.py` 添加 `active_agent` + `response_type` 字段
7. 创建 `src/graph/specialized_agents.py`（含确定性 router node + 5 个 agent node + 共享 loop + schema 校验 + repair）

**Phase 3 — Graph Assembly**
8. 创建 `src/graph/multi_agent_graph.py`（graph builder + run_multi_agent_stream）
9. 修改 `src/api/chat.py` 添加 mode="multi_agent"

## Verification

1. 启动 backend: `uvicorn src.api.chat:app --reload --port 8000`
2. 测试 mode="agent"（原有路径不受影响）
3. 测试 mode="multi_agent"：
   - "我想买个送朋友的礼物，预算200" → recommend_agent → recommendation_cards
   - "帮我找个双肩包" → search_agent → product_grid
   - "小米这个双肩包怎么样" → detail_agent → detail_card
   - "国家地理和小米双肩包哪个好" → compare_agent → comparison_table
4. 检查每个 agent 只收到分配的 tool schema（日志中可见）
5. 检查 response_type 正确传递到 SSE event

## Design Refinements

### Issue 7: Recommendation Agent 搜索策略

Recommend Agent 不自己生成 query，而是使用 preprocessing 阶段 `plan_search()` 预计算的 `search_plan`。

- `plan_search()` 已在 `src/agents/search_planner.py` 实现，preprocessing 时自动调用
- 结果存在 `state["search_plan"]`，注入到 Recommend Agent 的 system prompt
- Agent 调用 `multi_query_search` 时，`search_requests` 由 `specialized_agents.py` 的防御性注入从 `search_plan` 补全
- prompt 明确约束："调用 multi_query_search 时，直接使用系统注入的 search_requests，不要自己编造 query"

### Issue 8: Compare Agent 对比矩阵

Compare Agent 的输出从简单的 price/rating 扩展为多维对比矩阵：

新增维度（`ComparisonTableItem` schema 已更新）：
- `capacity`: 容量/尺寸
- `material`: 材质
- `style`: 风格
- `scenario_fit`: 适合场景
- `reputation_label`: 口碑标签

Compare Agent 的 prompt 详细列出了 9 个对比维度及其数据来源，要求基于工具返回的真实数据构建对比矩阵，数据缺失时填 null 而非编造。

### Issue 9: Detail Agent 防跑偏约束

Detail Agent prompt 新增三条硬约束：
1. "严禁跑偏：你是在'解释商品'，不是在'推荐商品'。不要推荐其他类似商品，除非用户明确要求"
2. "搜索到目标商品后，不要再搜索其他商品。流程是：product_search → product_detail_batch → review_summary → Final Answer"
3. "除非无法定位商品，否则不要重复调用 product_search"

### Issue 10: Search Agent 保持工具精简

暂不给 search_agent 加 `multi_query_search`。探索性模糊搜索（"有哪些适合通勤的包"）走 recommend_agent。search_agent 保持 3 个工具：`product_search`, `constraint_relaxation`, `ask_clarification`。
