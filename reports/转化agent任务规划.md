# ShoppingAgent 工作流 → 混合式 Agent 转化任务规划

> **目标**: 采用"确定性预处理 + ReAct 动态决策"的混合式 Agent 架构，将固定工作流中低风险、强结构化的步骤保留为确定性前置，将不确定的检索/追问/比价决策交给 ReAct Agent。

## 当前状态

现有架构是典型的 Pipeline/Workflow：
```
用户输入 → 意图识别 → 实体抽取 → 记忆召回 → 追问决策 → 混合检索 → 排序 → 生成文案
```
路径固定，缺少 Agent 的动态决策能力。

## 目标架构：确定性预处理 + ReAct 动态决策

```
用户输入
    ↓
┌───────────────────────────────────┐
│   确定性预处理层（固定执行）         │
│   1. intent_classifier             │
│   2. entity_extractor              │
│   3. memory_retriever              │
│   输出: intent, entities, memories │
└───────────────┬───────────────────┘
                ↓
┌───────────────────────────────────┐
│   Shopping ReAct Agent             │
│   输入: intent + entities + memories│
│   动态决策:                         │
│   - 是否追问？还是直接检索？         │
│   - 用什么检索策略？                 │
│   - 检索失败怎么处理？               │
│   - 比价时调用哪些详情工具？          │
│   - 是否需要评论摘要？               │
└───────────────┬───────────────────┘
                ↓
        ┌───────────────────────────────┐
        │   Agent 可调用的 Tools（高层）  │
        │ 1. product_search             │  ← 内部: 硬筛+向量+排序
        │ 2. product_detail_batch       │  ← 批量获取详情
        │ 3. price_compare              │
        │ 4. review_summary             │
        │ 5. constraint_relaxation      │
        │ 6. ask_clarification          │  ← 内部: slot_checker+生成问题
        └───────────────┬───────────────┘
                        ↓
                  最终回复
                        ↓
        ┌───────────────────────────────┐
        │   确定性后处理（固定执行）       │
        │ 1. extract_preference         │  ← 从对话中提取偏好候选
        │ 2. should_save_memory         │  ← 判断是否为长期偏好
        │ 3. memory_update              │  ← 满足条件才写入
        └───────────────────────────────┘
```

**设计原则**:
- 意图识别、实体抽取、记忆召回是低风险、强结构化任务，固定执行更稳定
- ReAct Agent 专注于"不确定决策"——检索策略选择、追问判断、约束放宽、多步比价等
- 记忆更新是确定性后处理，不交给 Agent 自由调用，避免临时需求误写为长期偏好

---

## 阶段一：确定性预处理 + 工具化

> 分两部分：(A) 将意图识别、实体抽取、记忆召回封装为确定性预处理节点（固定执行，非 Agent 工具）；(B) 将检索/追问/比价等动态决策模块封装为 Agent 可调用的 Tool。

### T1.1 定义 Tool Schema 规范

**文件**: `src/tools/schema.py`（新建）

- 定义 `ToolDef` dataclass：name, description, parameters (JSON Schema), return_type
- 定义 `tool_registry`：注册/发现/版本管理
- 参考现有 `src/skills/schema.py` 的 `SkillDefinition` 结构

### T1.2 确定性预处理节点（非 Agent Tool）

这三个模块不暴露为 Agent Tool，而是作为 Graph 的固定前置节点执行：

**文件**: `src/graph/preprocessing.py`（新建）

```python
async def node_preprocess(state: AgentState) -> dict:
    """确定性预处理：意图识别 + 实体抽取 + 记忆召回（并行执行）"""
    user_input = _get_user_input(state)
    user_id = state.get("user_id", "default_user")

    # 三者并行执行
    intent_task = classify_intent(user_input)
    entity_task = extract_entities(user_input)
    memory_task = recall(user_id, user_input) if should_recall(user_input) else []

    intent_result, entities, memories = await asyncio.gather(
        intent_task, entity_task, memory_task
    )

    intent, confidence, source = intent_result

    # 实体消歧（如果需要）
    if entities.get("ambiguous"):
        entities["_raw_query"] = user_input
        disambig_result = await disambiguate(entities)
        entities = disambig_result["entities"]

    return {
        "intent": intent,
        "entities": entities,
        "memory_chunks": memories if isinstance(memories, list) else [],
    }
```

**理由**: 意图识别和实体抽取是低风险、强结构化任务，固定执行保证稳定性。后续工具（检索、排序）依赖它们的输出作为输入，不应让 Agent 决定是否调用。

### T1.3 封装高层检索 Tool（Agent 可调用）

**文件**: `src/tools/product_search.py`（新建）
**依赖**: `src/retrieval/hybrid_retriever.py`, `src/agents/ranker.py`, `src/tools/search_tool.py`

```python
@tool("product_search")
async def product_search_tool(
    entities: dict,
    semantic_query: str,
    search_mode: str = "hybrid",  # "hybrid" | "filter_only" | "vector_only"
    top_k: int = 10,
) -> dict:
    """一站式商品检索：硬条件过滤 + 向量语义检索 + 多目标排序。

    内部流程：
    1. build_filter(entities) → Qdrant payload 预过滤
    2. hybrid_search(semantic_query, entities, top_k) → 向量检索
    3. rank(results, entities) → 多目标加权排序

    Args:
        entities: 结构化实体（category, brand, price_max, scenario 等）
        semantic_query: 语义检索文本（用户原始需求的关键词/描述）
        search_mode: 检索模式，hybrid=硬筛+向量，filter_only=仅硬筛，vector_only=仅向量
        top_k: 返回结果数量

    Returns:
        {"results": [...], "total": int, "filter_applied": bool, "latency_ms": float}
    """
```

**内部实现**: 调用 `hybrid_retriever.hybrid_search()` + `ranker.rank()`，Agent 无需关心底层细节。

### T1.4 封装批量详情和比价 Tools（Agent 可调用）

**文件**: `src/tools/product_detail.py`（新建）
**依赖**: `src/tools/search_tool.py`

```python
@tool("product_detail_batch")
async def product_detail_batch_tool(product_ids: list[str]) -> list[dict]:
    """批量获取商品详情（价格、评分、库存、特征等）。

    用于比价场景：Agent 传入多个 product_id，一次性获取所有详情。
    """
    products = load_products()
    id_set = set(product_ids)
    return [p for p in products if p.get("product_id") in id_set]

@tool("price_compare")
async def price_compare_tool(product_ids: list[str]) -> dict:
    """对比多个商品的价格、评分、评论摘要，生成结构化对比数据。

    Returns:
        {"products": [...], "price_range": (min, max), "best_value": product_id}
    """
```

### T1.5 封装约束放宽和追问 Tools（Agent 可调用）

**文件**: `src/tools/agent_tools.py`（新建）
**依赖**: `src/agents/clarification_engine.py`

```python
@tool("constraint_relaxation")
async def constraint_relaxation_tool(entities: dict, failed_reason: str) -> dict:
    """放宽检索约束，返回放宽后的 entities。

    策略：去掉品牌限制 → 扩大价格区间 → 去掉场景限制 → 仅保留品类。
    Agent 在检索结果过少时调用。
    """

@tool("ask_clarification")
async def ask_clarification_tool(entities: dict, asked_fields: list) -> dict:
    """检查缺失槽位并生成追问问题。

    内部调用 slot_checker 判断是否需要追问，然后生成问题文本。
    Returns: {"should_ask": bool, "questions": [...], "reason": str}
    """
```

### T1.6 封装评论摘要 Tool（Agent 可调用）

**文件**: `src/tools/review_tool.py`（新建）

```python
@tool("review_summary")
async def review_summary_tool(product_ids: list[str], aspects: list[str] = None) -> list[dict]:
    """批量提取商品评论的关键卖点和槽点摘要。"""
```

### T1.7 确定性后处理：记忆更新（非 Agent Tool）

记忆更新不暴露给 Agent，而是作为 Graph 的固定后处理节点执行：

**文件**: `src/graph/postprocessing.py`（新建）

```python
async def node_postprocess(state: AgentState) -> dict:
    """确定性后处理：偏好提取 + 记忆写入判断"""
    user_input = _get_user_input(state)
    response = state.get("final_response", "")
    entities = state.get("entities", {})
    user_id = state.get("user_id", "default_user")

    # Step 1: 从对话中提取偏好候选
    candidate = extract_preference_candidate(user_input, response, entities)

    # Step 2: 判断是否为长期偏好（非临时需求）
    if should_save_memory(candidate):
        await memory_update(user_id, user_input, response, entities)
        logger.info("memory_saved", user_id=user_id, category=entities.get("category"))

    return {}
```

**关键设计**: `should_save_memory` 区分长期偏好与临时需求：
- "帮我买便宜点的" → 临时需求，不保存
- "我一直喜欢简约风格" → 长期偏好，保存
- "我是油皮" → 用户属性，保存

**简历亮点**: 设计用户偏好记忆更新机制，对长期偏好与临时需求进行区分，避免误写入用户画像。

### T1.8 保留底层函数（内部使用，不暴露给 Agent）

以下函数保留为内部实现，由高层 Tool 内部调用：

| 底层函数 | 调用者 | 说明 |
|----------|--------|------|
| `product_filter_search()` | `product_search` 内部 | 硬条件过滤 |
| `product_vector_search()` | `product_search` 内部 | 向量语义检索 |
| `rerank_products()` | `product_search` 内部 | 多目标排序 |
| `slot_checker()` | `ask_clarification` 内部 | 缺失槽位检查 |

### T1.9 Tool Registry 集成

**文件**: `src/tools/registry.py`（新建）

- 统一注册所有 Agent Tools（6 个：product_search, product_detail_batch, price_compare, review_summary, constraint_relaxation, ask_clarification）
- 提供 `get_dynamic_tools() -> list[ToolDef]` 给 Agent 使用
- 支持按 name 查询

---

## 阶段二：ReAct Agent 核心

> 构建混合式 Graph：确定性预处理节点 → ReAct 动态决策循环。预处理结果（intent, entities, memories）作为 Agent 的初始上下文，Agent 只需决定后续的检索/追问/比价策略。

### T2.1 定义 Agent State

**文件**: `src/graph/agent_state.py`（新建）

```python
class AgentState(TypedDict):
    # === 对话基础 ===
    messages: Annotated[list, add_messages]     # 对话历史
    user_id: str                                 # 用户 ID

    # === 确定性预处理输出（固定节点写入） ===
    intent: str                                  # 意图分类结果
    entities: dict                               # 已提取的实体
    memory_chunks: list                          # 召回的记忆片段

    # === Agent 动态决策过程 ===
    search_results: list                         # 检索结果（Agent 或 fallback 都可能写入）
    user_profile: dict                           # 用户画像（从记忆推断）
    tool_calls_log: Annotated[list, _add_lists]  # 工具调用日志（观察记录）
    iteration: int                               # ReAct 循环次数
    max_iterations: int                          # 最大循环次数（默认 5）
    final_response: str | None                   # 最终回复（终止信号）
    asked_fields: list                           # 已追问过的字段（避免重复追问）
    used_fallback: bool                          # 是否走了兜底路径
```

### T2.2 ReAct Agent Prompt 设计

**文件**: `src/agents/react_prompt.py`（新建）

核心 System Prompt：
```
你是一个智能导购 Agent。系统已经为你完成了以下预处理：

用户意图：{intent}（置信度 {confidence}）
提取的实体：{entities}
用户历史记忆：{memory_summary}

你可以调用以下工具来完成后续决策：

{tool_descriptions}

遵循 ReAct 模式：
1. Thought: 分析当前状态，决定下一步
2. Action: 调用一个工具
3. Observation: 观察工具返回结果
4. 重复直到信息充足，然后给出 Final Answer

决策规则：
- 实体已完整 + 无歧义 → 调用 product_search 直接检索
- 实体缺失关键字段（如品类为空） → 调用 ask_clarification
- 记忆中有用户偏好 → 用记忆补全实体，不追问，直接检索
- 检索结果 < 3 → 调用 constraint_relaxation 放宽后重新 product_search
- 意图是 compare → 调用 product_detail_batch 获取详情，再调用 price_compare
- 每次只调用一个工具
- 不要重复调用已调用过的工具（相同参数）
```

### T2.3 构建混合式 Agent Graph（含兜底路径）

**文件**: `src/graph/shopping_agent.py`（新建）

```python
def build_shopping_agent_graph():
    graph = StateGraph(AgentState)

    # 确定性预处理（固定执行）
    graph.add_node("preprocess", node_preprocess)

    # ReAct 动态决策循环
    graph.add_node("react_loop", node_react_loop)

    # 兜底路径：复用 Agent 已有结果，走稳定 Pipeline
    graph.add_node("fallback", node_fallback)

    # 确定性后处理（固定执行）
    graph.add_node("postprocess", node_postprocess)

    # 固定流程：预处理 → Agent（或 fallback） → 后处理
    graph.set_entry_point("preprocess")
    graph.add_edge("preprocess", "react_loop")

    # Agent 内部循环：成功 → 后处理，失败 → 兜底
    graph.add_conditional_edges("react_loop", should_continue, {
        "continue": "react_loop",
        "end": "postprocess",
        "fallback": "fallback",
    })

    # 兜底路径 → 后处理
    graph.add_edge("fallback", "postprocess")

    # 后处理 → 结束
    graph.add_edge("postprocess", END)

    checkpointer = get_checkpointer("memory")
    return graph.compile(checkpointer=checkpointer)
```

**双路径流程**:
```
preprocess → react_loop → 成功 → postprocess → END
                       ↓ 失败
                    fallback → postprocess → END
```

- preprocess: 意图识别 + 实体抽取 + 记忆召回（并行）
- react_loop: Agent 动态调用 6 个工具
- fallback: 复用已有 entities/search_results，走稳定 Pipeline 兜底
- postprocess: 偏好提取 + 记忆写入判断

### T2.4 ReAct 循环节点

**文件**: `src/graph/react_node.py`（新建）

```python
async def node_react_loop(state: AgentState) -> dict:
    """ReAct 核心循环：Thought → Action → Observation

    输入: 预处理后的 intent, entities, memories
    输出: 工具调用结果 或 最终回复
    """
    llm = get_llm("react_agent")
    tools = get_dynamic_tools()  # 不包含 intent/entity/memory，只有决策类工具

    # 构建 prompt（包含预处理结果 + 工具描述 + 对话历史）
    messages = build_react_messages(state, tools)

    # LLM 决定下一步
    response = await llm.chat(messages, tools=tool_schemas)

    if response.get("tool_calls"):
        tool_call = response["tool_calls"][0]
        result = await execute_tool(tool_call["name"], tool_call["args"])
        return {
            "tool_calls_log": [{"tool": tool_call["name"], "args": tool_call["args"], "result": result}],
            "iteration": state["iteration"] + 1,
        }
    else:
        return {"final_response": response["content"], "iteration": state["iteration"] + 1}
```

### T2.5 终止条件判断 + 兜底触发

**文件**: `src/graph/react_node.py`（同上）

```python
def should_continue(state: AgentState) -> str:
    """判断 ReAct 循环是否应该终止，或触发兜底"""
    # 1. LLM 给出了最终回复 → 正常结束
    if state.get("final_response"):
        return "end"
    # 2. 达到最大循环次数 → 触发兜底
    if state["iteration"] >= state.get("max_iterations", 5):
        logger.warning("react_max_iterations", iteration=state["iteration"])
        return "fallback"
    # 3. 连续两次相同工具调用（死循环检测） → 触发兜底
    log = state.get("tool_calls_log", [])
    if len(log) >= 2 and log[-1]["tool"] == log[-2]["tool"] and log[-1]["args"] == log[-2]["args"]:
        logger.warning("react_loop_detected", tool=log[-1]["tool"])
        return "fallback"
    return "continue"
```

### T2.6 兜底节点：复用已有结果走稳定 Pipeline

**文件**: `src/graph/fallback.py`（新建）

```python
async def node_fallback(state: AgentState) -> dict:
    """兜底路径：复用 Agent 已有结果，走稳定 Pipeline 生成回复。

    不是"完全重新跑一遍"，而是：
    - 复用已有的 intent, entities, memory_chunks（预处理结果）
    - 复用已有的 search_results（如果 Agent 已经检索过）
    - 只补充缺失的步骤（如还没检索就补检索，还没排序就补排序）
    """
    entities = state.get("entities", {})
    search_results = state.get("search_results", [])
    intent = state.get("intent", "")
    memories = state.get("memory_chunks", [])

    # Step 1: 如果 Agent 还没检索过，补一次检索
    if not search_results:
        user_input = _get_user_input(state)
        search_result = await hybrid_search(user_input, entities, top_k=10)
        search_results = search_result.get("results", [])

    # Step 2: 如果结果还没排序，补排序
    if search_results and not any(p.get("rank_score") for p in search_results):
        search_results = rank(search_results, entities=entities)

    # Step 3: 生成推荐文案（复用 explainer 逻辑）
    if search_results:
        explanation = _generate_fallback_explanation(search_results[:3], entities)
    else:
        explanation = "抱歉，暂时没有找到符合条件的商品，建议放宽筛选条件。"

    logger.info("fallback_used",
                has_results=len(search_results) > 0,
                result_count=len(search_results),
                entities=entities)

    return {
        "search_results": search_results,
        "final_response": explanation,
        "used_fallback": True,
    }


def _generate_fallback_explanation(products: list, entities: dict) -> str:
    """兜底文案生成：简单直接，不过度优化"""
    lines = ["为你推荐：\n"]
    for i, p in enumerate(products[:3], 1):
        name = p.get("name", "商品")
        price = p.get("final_price", p.get("price", 0))
        platform = p.get("platform_id", "")
        platform_tag = f" [{platform}]" if platform else ""
        lines.append(f"{i}. {name}{platform_tag} — ¥{price}")
    lines.append("\n以上是根据你的需求筛选的商品，供参考。")
    return "\n".join(lines)
```

### T2.6 工具执行器

**文件**: `src/graph/tool_executor.py`（新建）

```python
async def execute_tool(name: str, args: dict) -> dict:
    """统一工具执行入口，包含错误处理和日志"""
    tool = get_tool_by_name(name)
    try:
        result = await tool.func(**args)
        logger.info("tool_executed", tool=name, args=args, result_type=type(result).__name__)
        return {"success": True, "data": result}
    except Exception as e:
        logger.error("tool_failed", tool=name, error=str(e))
        return {"success": False, "error": str(e)}
```

---

## 阶段三：动态决策增强

> 让 Agent 具备检索失败自修正、动态追问、多步比价等高级能力。这些能力通过 ReAct 循环自然涌现，本阶段主要是 Prompt 调优和边界 case 处理。

### T3.1 检索失败自修正

**逻辑**: Agent 观察到 product_search 返回结果过少时，自主决定放宽约束后重试

```
Thought: product_search 返回了 2 个结果，太少。需要放宽约束。
Action: constraint_relaxation(entities={...}, failed_reason="结果过少")
Observation: 放宽了品牌限制，价格区间从 200 扩大到 500
Thought: 用放宽后的条件重新检索
Action: product_search(entities=放宽后的entities, semantic_query="衬衫 通勤")
Observation: 返回 8 个结果，质量不错
Thought: 结果充足，生成推荐
Final Answer: ...
```

### T3.2 动态追问决策

**逻辑**: Agent 根据预处理输出的 entities 完整度和 memory_chunks 动态决定是否追问

```
Thought: 预处理提取到品类=衬衫，但没有性别。记忆里有"男性偏好"，可以补全。
         预算没说，但历史偏好是 100-300。不需要追问，直接用推断值检索。
Action: product_search(entities={category:"衬衫", gender:"男", price_max:300}, semantic_query="衬衫")
Observation: 返回 6 个结果
Final Answer: ...
```

对比固定工作流的机械追问，Agent 可以：
- 从记忆补全缺失字段
- 根据场景判断是否必须追问（泛推荐不需要精确预算）
- 先给默认推荐，用户不满意再追问

### T3.3 多步比价 Agent

**逻辑**: 预处理识别 intent=compare 时，Agent 用高层工具完成比价

```
Thought: 意图是比价，实体里提到了 A 和 B 两个商品。先批量获取详情。
Action: product_detail_batch(product_ids=["A", "B"])
Observation: [{name:"A", price:199, rating:4.5, ...}, {name:"B", price:259, rating:4.8, ...}]
Thought: 有详情了，再获取价格对比数据
Action: price_compare(product_ids=["A", "B"])
Observation: {price_diff:60, rating_diff:0.3, best_value:"A"}
Thought: 信息充足，生成对比表和推荐结论
Final Answer: ...
```

对比细粒度方案，Agent 只需 2 次工具调用（detail_batch + compare），而非 3-4 次。

### T3.4 SSE 流式输出适配

**文件**: `src/api/chat.py`（修改）

- 复用现有 SSE 事件格式（intent, entities, clarification, results, explanation, done）
- 新增 `tool_call` 事件，前端可展示 Agent 推理过程（可选）
- `run_shopping_stream` 改为调用 Agent Graph
- 预处理阶段的 intent/entities 事件照常发送

---

## 阶段四：API 与前端适配

### T4.1 修改 API 入口

**文件**: `src/api/chat.py`（修改）

```python
# 原来
from src.graph.shopping_graph import run_shopping_stream

# 改为
from src.graph.shopping_agent import run_agent_stream
```

- `POST /api/chat` 调用 Agent Graph
- 保持 SSE 事件格式向后兼容
- 新增 `tool_call` 事件类型（可选，前端可展示推理链）

### T4.2 前端可选：展示推理过程

**文件**: `frontend/src/components/ChatBox.tsx`（可选修改）

- 新增 `ToolCallBubble` 组件，展示 Agent 调用了哪些工具
- 折叠式展示，不影响主交互流程

---

## 阶段五：测试与验证

### T5.1 Tool 单元测试

**文件**: `tests/test_tools.py`（新建）

- 测试每个 Tool 的输入输出格式
- 测试错误处理（无效输入、超时等）

### T5.2 ReAct 循环测试

**文件**: `tests/test_react_agent.py`（新建）

- 测试正常路径：用户查询 → Agent 调用 3-5 个工具 → 返回推荐
- 测试追问路径：模糊查询 → Agent 决定追问 → 用户回复 → 继续
- 测试自修正路径：检索失败 → 放宽约束 → 重新检索
- 测试比价路径：比价查询 → 多步详情获取 → 对比表
- 测试终止条件：最大循环次数、死循环检测

### T5.3 端到端测试

**文件**: `tests/test_e2e_agent.py`（新建）

- 模拟完整用户会话（多轮对话）
- 验证 SSE 事件流格式正确
- 验证 HITL 下单流程仍正常工作

---

## 实施顺序建议

```
阶段一（预处理 + 工具化）  → 阶段二（ReAct 核心）  → 阶段三（动态决策）  → 阶段四（API 适配） → 阶段五（测试）
T1.1-T1.7                  T2.1-T2.4              T3.1-T3.4             T4.1-T4.2             T5.1-T5.3
约 2-3 天                   约 2-3 天               约 1-2 天              约 1 天                约 1 天
```

**总计**: 约 7-10 天

---

## 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 整体架构 | 确定性预处理 + ReAct 动态决策 | 比纯 ReAct 更稳定，比纯工作流更灵活 |
| Agent 框架 | LangGraph ReAct | 项目已用 LangGraph，迁移成本低 |
| Tool 协议 | LangChain Tool 兼容 | 生态成熟，可复用社区 Tool |
| Tool 粒度 | 高层聚合 Tool | 减少 Agent 循环轮次，降低延迟；底层函数内部调用 |
| 最大循环次数 | 5 | 平衡响应时间和推理深度 |
| 意图识别 | 确定性预处理（固定执行） | 低风险、强结构化，后续工具依赖其输出 |
| 实体抽取 | 确定性预处理（固定执行） | 同上，且需要消歧处理 |
| 记忆召回 | 确定性预处理（固定执行） | 并行执行不增加延迟，为 Agent 提供上下文 |
| 检索 | 高层 Tool（product_search） | 内部完成硬筛+向量+排序，Agent 不关心底层 |
| 追问 | 高层 Tool（ask_clarification） | 内部完成槽位检查+问题生成，Agent 只需决定是否追问 |
| 比价 | 高层 Tool（product_detail_batch + price_compare） | 批量获取，减少调用轮次 |
| 记忆更新 | 确定性后处理（非 Agent Tool） | 避免临时需求误写为长期偏好 |
| 最终文案生成 | Agent 直接生成 | 不过度工具化，保留 LLM 的自然语言能力 |
| 前端推理链展示 | 可选 | 不影响核心功能，作为增强体验 |

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| LLM 调用次数增加 | 延迟上升、成本增加 | 预处理层不走 LLM（Semantic Router）；限制 max_iterations=5 |
| Agent 死循环 | 用户体验差 | 死循环检测 + 强制终止 |
| 工具调用失败 | 推理链中断 | 每个 Tool 有 try-catch，返回 error 给 Agent 决策 |
| Prompt 膨胀 | Token 超限 | 工具描述精简；对话历史压缩；预处理结果摘要化 |
| 预处理延迟 | 首字响应变慢 | intent/entity/memory 三者并行执行（asyncio.gather） |
| 回归风险 | 原有功能异常 | 保留原 shopping_graph 作为 fallback |

---

## 简历描述

```
采用"确定性预处理 + ReAct 动态决策"的混合式 Agent 架构，构建智能导购系统。
意图识别、实体抽取和记忆召回作为确定性预处理层固定执行，保证基础信息提取的稳定性；
检索策略选择、动态追问、约束放宽和多步比价等不确定决策交由 ReAct Agent 动态规划，
实现推理→工具调用→观察→再决策的自适应执行框架。
```

技术亮点：
- 混合式 Agent 架构：确定性预处理 + ReAct 动态决策 + 确定性后处理，兼顾稳定性和灵活性
- 检索失败自修正：Agent 可根据召回数量动态调整检索策略（约束放宽、关键词重写）
- 多步比价推理：Agent 自主规划详情获取、评论摘要、价格对比等多步调用
- 动态追问：结合用户画像和场景判断是否追问，避免固定规则的无效打断
- 并行预处理：意图识别、实体抽取、记忆召回三者并行，不增加额外延迟
- 偏好记忆机制：区分长期偏好与临时需求，避免误写入用户画像
