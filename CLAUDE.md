# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ShopMesh — 基于 LangGraph 的智能导购系统，**双语言微服务架构**：Java 控制面（Auth/CRUD）+ Python Agent 引擎（LangGraph/Tools/Memory）。通过 Traefik 网关路径级分流，RabbitMQ 异步通信。

**当前阶段**: Phase 5（安全体系）。Agent 架构优化已完成。

## Architecture Optimization (已完成)

基于 `docs/references/learn-claude-code/` S01-S20 对比分析，已完成六项架构优化：

| 模块 | 文件 | 说明 |
|------|------|------|
| P0-1 Error Recovery | `src/graph/specialized_agents.py` | 指数退避重试、prompt_too_long 恢复、fallback 模型 |
| P0-2 Subagent 隔离 | `src/graph/dag_executor.py` | 精简状态传递、30 轮安全限制、禁止递归 |
| P1-1 Task 认领 | `src/graph/task_store.py`, `orchestrator.py` | 硬编码 missing_fields 检查、Redis DAG 持久化 |
| P1-2 Context Compact | `src/memory/context_compactor.py` | 四层压缩管线 + 熔断器 |
| P2-1 Hooks 系统 | `src/graph/hooks.py`, `builtin_hooks.py` | 四个事件点、可扩展钩子机制 |
| P2-2 Nag Reminder | `src/graph/specialized_agents.py` | 连续 3 轮无工具调用时注入提醒 |

## Tech Stack

- **Java 控制面**: Spring Boot 3.4 / Spring Security / Spring Data JPA / Flyway / jjwt (RSA-256)
- **Python Agent**: Python 3.11+ (venv uses 3.12) / LangGraph / FastAPI / httpx / Celery
- **Vector DB**: Qdrant + BGE-M3 (Ollama)
- **Storage**: PostgreSQL 16 (shared, Flyway schema) / Redis 7 (session + Celery result backend)
- **Message Queue**: RabbitMQ (4 queues: memory, cleanup, default, events)
- **Gateway**: Traefik v3 (路径分流: /api/auth → Java, /api/chat → Python)
- **Frontend**: Next.js 14 / React 18 / Tailwind CSS / Zustand
- **Logging**: structlog (JSON)

## Common Commands

### Python Backend
```bash
# Activate venv
source .venv/Scripts/activate   # Git Bash
.venv\Scripts\activate          # CMD

# Run API server
uvicorn src.api.chat:app --reload --port 9000

# Run tests
python -m pytest tests/                                    # all
python -m pytest tests/test_guardrails_integration.py      # single file
python -m pytest tests/test_cost_tracker.py::TestCostCalculation::test_input_only  # single test

# CLI
python main.py "帮我找护肤品"              # single query
python main.py --stream "帮我找护肤品"     # streaming

# Build vector index (requires Qdrant + Ollama)
python scripts/build_index.py

# Generate mock data
python scripts/generate_mock_products.py --count 5000 --output data/mock_products_5k.json
```

### Java (shopmesh-java/)
```bash
cd shopmesh-java
mvn test                    # all tests (19 pass, uses H2 in-memory)

# Build and run (jar 方式启动，避免 spring-boot:run 文件锁问题)
mvn clean package -DskipTests
java -jar target/shopmesh-api-0.1.0.jar   # http://localhost:18080
```

### Frontend (frontend/)
```bash
cd frontend
npm run dev     # http://localhost:3000
npm run build
npm run lint
```

### Docker Compose
```bash
docker compose up           # all 9 services (traefik, java-api, python-agent, celery-worker, celery-beat, postgres, redis, qdrant, rabbitmq)
docker compose up -d        # detached
```

## Architecture

### 双语言微服务分层

```
Client → Traefik (:80)
  ├── /api/auth/*, /api/auth/api-keys/*, /.well-known/jwks.json  → Java :18080
  └── /api/chat/*, /api/conversations/*, /api/behavior/*, /api/tasks/*  → Python :9000
```

- **Java 控制面** (`shopmesh-java/`): Auth (register/login/refresh/me)、API Key CRUD、RSA JWT 签发、JWKS 公钥端点、用户事件发布
- **Python Agent 引擎** (`src/`): LangGraph Agent、Tools、Memory、Celery 后台任务
- **共享**: PostgreSQL (Java Flyway 管理 schema)、Redis、Qdrant、RabbitMQ
- **数据库迁移**: **Flyway 是 schema 的唯一 source of truth** (`shopmesh-java/src/main/resources/db/migration/`)。Java 启动时自动迁移 (`spring.flyway`, `ddl-auto=validate`)。Python **不写迁移**、只通过 SQLModel 读写既有 schema。改表 = 新增一个 Flyway `V{n}__*.sql`。详见 [docs/MIGRATIONS.md](docs/MIGRATIONS.md)。

### JWT 跨语言信任

Java 签发 RS256 JWT → Python 通过 JWKS 端点拉取公钥验证。`src/auth/jwks_client.py` 缓存公钥 1 小时自动刷新。Python 不再签发 JWT。

### Python Agent Pipeline (multi_agent_graph.py — 当前默认)

```
preprocess (intent + entity + memory 并行)
  → agent_router (确定性 if/else)
  → search_recommend_agent / detail_compare_agent (ReAct loop)
  → postprocess (记忆更新 + 偏好提取)
  → END
```

每个 Agent 有独立 prompt、tool 子集、max_iterations（config.yaml `agents` 下）。Agent 路由基于 intent 分类结果。

### Agent 可调用 Tools (src/tools/)

| Tool | 功能 |
|------|------|
| product_search | 一站式检索：硬筛+向量+排序 |
| multi_query_search | 多查询并行检索+去重+重排 |
| product_detail_batch | 批量获取商品详情 |
| price_compare | 多商品比价 |
| review_summary | 评论摘要 |
| constraint_relaxation | 放宽检索约束 |
| ask_clarification | 缺失槽位检查+追问 |

Tools 通过 `ToolDef` 注册到 `tool_registry`，`execute_tool()` 是统一入口（含 permission + rate limit + hooks 检查）。

### Guardrails 四层防护 (src/security/)

- **input_guard**: prompt 注入检测 + 长度限制 + intent 白名单（接入 `/api/chat` 端点）
- **output_guard**: 幻觉检测 + 价格边界 + 覆盖率验证（接入 specialized_agents + react_node 最终输出）
- **permission**: 权限等级 (READ/WRITE/SENSITIVE) + 速率限制 10次/分钟/用户（接入 tool_executor）
- **data_guard**: PII 脱敏 + 日志消毒 + 响应文本 mask（接入 tool_executor 日志 + postprocessing 响应）

### Memory 系统 (src/memory/)

四层记忆架构：
- **L1 Working Memory**: 进程内 dict，单次执行
- **L2a Session Memory**: Redis List 滑动窗口（5 轮），超限 LLM 压缩
- **L2c Vector Memory**: Qdrant 统一集合 `user_long_term_memories`，BGE-M3 embedding，读时衰减
- **L3 User Profile**: PostgreSQL `user_profiles` 表，per-user per-category，EMA 价格范围 + 品牌偏好

### Celery 后台任务 (src/tasks/)

| 队列 | 任务 | 说明 |
|------|------|------|
| memory | trim_session, write_vector_memory, batch_classify_preferences, update_profile_preference, save_conversation_message, process_behavior_signal | 记忆和偏好操作 |
| cleanup | cleanup_expired_memories, cleanup_user_memories | 定期清理（Beat 每日调度） |
| events | handle_user_registered, handle_user_profile_updated | Java → Python 事件消费 |
| default | (fallback) | 未路由的任务 |

### LLM 成本追踪 (src/observability/cost_tracker.py)

- `LLMClient.chat()` 和 `chat_stream()` 自动捕获 token usage
- 按 tenant 聚合，structlog 记录 + PostgreSQL `llm_usage` 表持久化
- 日消费超阈值 → structlog warning 告警
- `GET /api/admin/costs` 查询接口

### State Management (graph/state.py)

`ShoppingState` 字段分类：
- **Reducer fields** (`Annotated[list, add_messages]`): 并行安全追加
- **Exclusive fields**: 各 agent 独占写入
- **Read-write fields**: 共享计数器

State 只存工作记忆，执行日志走 structlog。

### HITL (Human-in-the-Loop) Order Flow

LangGraph `interrupt()` 机制：prepare_order → interrupt("请确认下单") → 前端 POST /api/chat/resume → confirm/cancel。

### Hooks 系统 (src/graph/hooks.py)

四个事件点，支持可扩展的钩子机制：
- `pre_tool_use`: 工具执行前（权限检查、日志）
- `post_tool_use`: 工具执行后（输出检查、副作用）
- `pre_llm_call`: LLM 调用前（上下文注入）
- `post_llm_call`: LLM 调用后（统计）

通过 `register_hook(event, callback)` 注册，`trigger_hooks(event, **kwargs)` 触发。Hook 返回 `HookResult(block=True)` 可阻止执行。

### Context Compact (src/memory/context_compactor.py)

四层压缩管线（便宜的先跑，贵的后跑）：
- L1 snip_compact: 截断旧消息，保留头尾（0 API）
- L2 micro_compact: 旧 tool_result 替换为占位符（0 API）
- L3 tool_result_budget: 大输出持久化到 Redis（0 API）
- L4 compact_history: LLM 生成摘要（1 API）

`CompactionCircuitBreaker` 熔断器：连续 3 次压缩失败后停止重试。

### Task Store (src/graph/task_store.py)

DAG 任务的 Redis 持久化，支持跨会话恢复：
- Key: `dag:{session_id}:{dag_id}`, `task:{session_id}:{dag_id}:{task_id}`
- TTL: 24 小时
- 语义: claim_task（依赖阻塞检查）、complete_task（解锁下游）

## Development Guidelines

- **Config**: 一切在 `config.yaml`，密钥用 `${ENV_VAR}`（`src/config.py` 加载时自动解析环境变量）
- **Schema 变更**: 只加 Flyway `V{n}__*.sql`（Java 侧）；Python 侧不再有 Alembic。SQLModel 模型 (`src/db/models.py`) 需与 Flyway schema 保持一致，仅用于 ORM 读写和测试建表，不驱动迁移。
- **LLM/Embedding**: 必须 async（`asyncio.to_thread` 包装同步调用）
- **State**: 只存工作记忆，不存执行日志
- **Commit messages**: `<动词>: <简述>` (e.g., `fix: 修复 LLM JSON 解析失败`)
- **Problem reports**: 记录到 `reports/problem.md`
- **开发节奏**: 用户控制，不自动推进下一任务
- **转化原则**: 原有 shopping_graph.py 及 agents/ 函数**不删除、不修改**，新旧架构通过 API `mode` 参数切换
- **测试命名**: 测试文件 `test_t05.py` ~ `test_t36.py` 对应开发任务编号，功能集成测试用 `test_*_integration.py` / `test_p*_e2e.py`

## Forbidden

- Hardcoded API keys / thresholds / model names in code
- Execution logs in State (use structlog)
- Calling sentence_transformers on the main thread
- Modifying or deleting the original shopping_graph.py or agents/ functions
- Mixing execution logs into State fields
