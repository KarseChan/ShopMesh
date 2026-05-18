# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

基于 LangGraph 的智能导购系统，正在从固定 Pipeline 工作流改造为**混合式 Agent 架构**（确定性预处理 + ReAct 动态决策）。

**当前任务**: 参照 `reports/转化agent任务规划.md` 执行工作流 → Agent 转化。分 5 个阶段：
1. 确定性预处理 + 工具化（T1.1-T1.9）
2. ReAct Agent 核心（T2.1-T2.6）
3. 动态决策增强（T3.1-T3.4）
4. API 与前端适配（T4.1-T4.2）
5. 测试与验证（T5.1-T5.3）

**目标架构**:
```
用户输入 → 确定性预层（intent + entity + memory，并行）→ ReAct Agent（动态调用 Tools）→ 确定性后处理（记忆更新）→ 最终回复
```

## Tech Stack

- **Backend**: Python 3.11+ / LangGraph / FastAPI / httpx
- **Vector DB**: Qdrant + BGE-M3 (Ollama) for product search & intent routing
- **Storage**: PostgreSQL (SQLModel) / Redis (session)
- **Frontend**: Next.js 14 / React 18 / Tailwind CSS / Zustand
- **Logging**: structlog (JSON format)

## Common Commands

### Backend
```bash
# Activate venv first
source .venv/Scripts/activate   # Git Bash
.venv\Scripts\activate          # CMD

# Run API server
uvicorn src.api.chat:app --reload --port 8000

# Run tests (pytest)
python -m pytest tests/                    # all tests
python -m pytest tests/test_scaffold.py    # single file
python -m pytest tests/test_scaffold.py::test_config_yaml_loads  # single test

# Build vector index (requires Qdrant + Ollama running)
python scripts/build_index.py
python scripts/build_index.py --data data/mock_data.json --collection products

# Generate mock product data
python scripts/generate_mock_products.py --count 5000 --output data/mock_products_5k.json
```

### Frontend
```bash
cd frontend
npm run dev     # dev server (default http://localhost:3000)
npm run build   # production build
npm run lint    # ESLint
```

## Architecture

### 原有工作流 (shopping_graph.py) — 保留不动

```
classify_intent → [extract_entities ∥ recall_memory] → should_clarify?
    yes → clarify → END (wait for user reply, loop back next turn)
    no  → hybrid_retrieve → promotion_calculate → rank → explain → END
```

- `classify_intent`: Semantic Router (fast, embedding similarity) → LLM fallback (slow)
- `extract_entities` + `recall_memory`: run in parallel via LangGraph fan-out
- `should_clarify`: priority = missing_degree × discrimination_power, max 3 rounds
- `hybrid_retrieve`: Qdrant vector search with payload pre-filter (not post-filter)
- `rank`: multi-objective weighted fusion (relevance/price/reputation/timeliness/personalization)

### 新架构：混合式 Agent (shopping_agent.py)

```
preprocess → react_loop → 成功 → postprocess → END
                       ↓ 失败
                    fallback → postprocess → END
```

**确定性预处理层** (graph/preprocessing.py):
- `node_preprocess`: intent + entity + memory 并行执行，输出到 state
- 低风险、强结构化，固定执行保证稳定性

**ReAct 动态决策** (graph/react_node.py):
- Agent 根据预处理结果动态调用 Tools
- 最大循环 5 次，含死循环检测
- 终止条件：final_response / max_iterations / loop_detected → fallback

**Agent 可调用的 Tools** (src/tools/):
| Tool | 文件 | 功能 |
|------|------|------|
| product_search | tools/product_search.py | 一站式检索：硬筛+向量+排序 |
| product_detail_batch | tools/product_detail.py | 批量获取商品详情 |
| price_compare | tools/product_detail.py | 多商品比价 |
| review_summary | tools/review_tool.py | 评论摘要 |
| constraint_relaxation | tools/agent_tools.py | 放宽检索约束 |
| ask_clarification | tools/agent_tools.py | 缺失槽位检查+追问 |

**底层函数保留**（由 Tools 内部调用，不暴露给 Agent）:
- `product_filter_search()` → product_search 内部
- `product_vector_search()` → product_search 内部
- `rerank_products()` → product_search 内部
- `slot_checker()` → ask_clarification 内部

**确定性后处理** (graph/postprocessing.py):
- 偏好提取 + 长期偏好 vs 临时需求判断 + 记忆写入

### State Management (graph/state.py)

`ShoppingState` is a TypedDict with three field categories:
- **Reducer fields** (`Annotated[list, add_messages]`): parallel-safe append (messages, tool_calls, errors)
- **Exclusive fields**: each agent writes its own (intent, entities, search_results, ranked_results, etc.)
- **Read-write fields**: shared counters (clarification_count, asked_fields)

State only stores working memory. Execution logs go to structlog, NOT state.

### HITL (Human-in-the-Loop) Order Flow

Uses LangGraph's native `interrupt()` mechanism:
1. `node_prepare_order` → prepares order details
2. `node_confirm_order` → calls `interrupt("请确认下单")`, graph pauses
3. Frontend sends `POST /api/chat/resume` with `Command(resume=True/False)`
4. Graph resumes from checkpoint, executes confirm or cancel

### Intent Classification (router/)

Two-tier: Semantic Router (Qdrant embedding similarity, threshold 0.80) → LLM fallback.
Intent samples stored in `data/intent_samples.json`, indexed into Qdrant collection `intent_samples`.

### Memory System (memory/)

L2c vector memory: per-user Qdrant collection `memory_{user_id}`. Triggered by reference words (上次/那个/之前) or cross-category jumps. Fire-and-forget writes after each recommendation.

### Security (security/)

- Input guard: prompt injection detection (regex), length limit (500 chars), intent whitelist
- Output guard: filters sensitive info from LLM responses
- Data guard: PII protection

## Development Guidelines

- **Config**: everything in `config.yaml`, use `${ENV_VAR}` for secrets (resolved at load time)
- **LLM/Embedding calls**: must be async (`asyncio.to_thread` for sync wrappers)
- **State**: working memory only, no execution logs
- **Parallel fields**: use `Annotated[list, add]` reducer for fields written by parallel nodes
- **Commit messages**: `<动词>: <简述>` (e.g., `fix: 修复 LLM JSON 解析失败`)
- **Problem reports**: write issue + fix to `reports/problem.md` after each fix
- **Development pace**: controlled by user, do not auto-advance to next task
- **转化原则**: 原有工作流函数（shopping_graph.py 及其依赖的 agents/）**不删除、不修改**，只额外新增 Tools 和新 Graph。新旧架构可共存，通过 API 切换

## Forbidden

- Hardcoded API keys / thresholds / model names in code
- Execution logs in State (use structlog)
- Calling sentence_transformers on the main thread (use asyncio.to_thread)
- Auto-invoking skills unless explicitly requested
