# SSE 流式传输改进方案

> 日期：2026-05-21
> 状态：实施中

## Context

当前 SSE 流式传输是"伪流式"：后端用 `StreamingResponse`，但关键内容（产品结果、推荐理由）在整个图执行完毕后才一次性推送。用户看到的是 loading → 长时间等待 → 产品卡片+推荐理由同时出现。中间只有 `intent`、`entities`、`tool_call` 等对用户无实际价值的中间事件。

## 问题根因

1. **results 延迟推送**：agent 节点完成时搜索结果已在 output 中，但代码在 `astream_events` 循环结束后、通过 `aget_state` 取最终 state 时才发
2. **explanation 未逐 token 流式**：LLM 调用使用 `resp.json()` 非流式方式，`final_response` 作为完整文本一次性返回
3. **aget_state 阻塞**：`astream_events` 循环结束后额外调用 `await graph.aget_state(config)` 提取最终状态

## 改进策略

分两步走，不改动图节点内部逻辑，只改流式事件推送层：

### Step 1: results 提前 yield（低风险）

在 `astream_events` 循环中，检测到 agent 节点完成（`on_chain_end`）时，从 output 中提取搜索结果并立即 yield `results` 事件。

### Step 2: explanation 逐 token 流式（中风险）

图执行完成后，不直接发送已有的 `final_response`，而是：
1. 从 `tool_calls_log` 中提取产品搜索结果作为上下文
2. 用 `LLMClient.chat_stream()` 发起一次新的流式 LLM 调用，生成推荐总结
3. 逐 token yield `explanation_delta` 事件，前端拼接实现打字机效果

## 修改文件清单

### 后端（4 个文件）

#### 1. `src/models/llm_client.py` — 新增 `chat_stream()` 方法

在 `LLMClient` 类中新增流式方法，使用 `httpx.AsyncClient.stream("POST", ...)` + `resp.aiter_lines()` 解析 OpenAI SSE 格式。

#### 2. `src/graph/stream_utils.py` — 新增流式辅助函数

提取公共逻辑，避免 multi_agent_graph.py 和 shopping_agent.py 重复代码：
- `_extract_search_results_from_output(output)`: 从 agent output 提取搜索结果
- `_extract_product_context(state_values)`: 从最终 state 提取产品信息文本
- `build_explanation_messages(state_values)`: 构建 explanation 流式调用的 messages
- `stream_explanation(state_values)`: 封装流式 explanation 生成

#### 3. `src/graph/multi_agent_graph.py` — 修改 `run_multi_agent_stream()`

- results 提前 yield：在 agent 节点完成时立即提取搜索结果并 yield
- explanation 流式：用 `stream_explanation()` 逐 token yield `explanation_delta`
- 移除 `aget_state` 阻塞

#### 4. `src/graph/shopping_agent.py` — 同样修改 `run_agent_stream()`

同 multi_agent_graph.py 的改动模式。

### 前端（2 个文件）

#### 5. `frontend/src/hooks/useChatStream.ts` — 处理新事件

- 新增 `explanation_delta` 事件：追加 delta 到 currentContent
- `isLoading` 在收到 `done` 时才设为 false

#### 6. `frontend/src/components/ChatBox.tsx` — 流式打字光标

- 在消息内容末尾添加闪烁光标（纯 CSS 动画），流式过程中显示
- 不引入新依赖

## 不改动的部分

- 图节点内部逻辑不变
- ReAct 循环中的 LLM 调用仍为非流式
- `run_shopping_stream()`（workflow 模式）暂不改
- 不引入新的 npm 依赖
