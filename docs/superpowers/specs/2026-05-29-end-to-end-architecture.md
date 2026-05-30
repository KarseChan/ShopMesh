# ShopMesh 端到端技术架构说明

> 基于 LangGraph 的智能导购系统 — 双语言微服务架构全链路技术解析

## 1. 架构概述

ShopMesh 采用 **Java 控制面 + Python Agent 引擎** 的双语言微服务架构。Java 负责认证、用户管理和 CRUD；Python 负责 LangGraph Agent 编排、工具调用和记忆管理。两者通过 Traefik 网关路径级分流，共享 PostgreSQL，通过 RabbitMQ 异步通信，JWT 跨语言信任通过 RSA + JWKS 实现。

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Docker Compose                              │
│                                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐              │
│  │ Traefik  │    │  Java :8080  │    │ Python :9000 │              │
│  │   :80    │───▶│  (控制面)     │    │  (Agent引擎)  │              │
│  └──────────┘    └──────────────┘    └──────────────┘              │
│       │                │                     │                      │
│       │          ┌─────┴─────┐         ┌─────┴──────┐              │
│       │          ▼           ▼         ▼            ▼              │
│       │    ┌──────────┐ ┌────────┐ ┌───────┐ ┌──────────┐         │
│       │    │PostgreSQL│ │RabbitMQ│ │ Redis │ │  Qdrant  │         │
│       │    │   :5432  │ │ :5672  │ │ :6379 │ │  :6333   │         │
│       │    └──────────┘ └────────┘ └───────┘ └──────────┘         │
│       │                   │                                        │
│       │              ┌────┴────┐                                   │
│       │              ▼         ▼                                   │
│       │      ┌────────────┐ ┌────────────┐                        │
│       │      │Celery Worker│ │Celery Beat │                        │
│       │      │(4队列消费)  │ │(定时调度)   │                        │
│       │      └────────────┘ └────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. 技术栈总览

| 层级 | 技术 | 版本 | 职责 |
|------|------|------|------|
| **网关** | Traefik | v3.2 | 路径级分流、安全头、网关限流 |
| **控制面** | Spring Boot | 3.4.x | Auth、JWT 签发、API Key CRUD |
| **Agent 引擎** | FastAPI + LangGraph | — | Agent 编排、工具调用、流式输出 |
| **数据库** | PostgreSQL | 16 | 共享 schema (Flyway 管理)、9 张表 |
| **缓存/会话** | Redis | 7 | Session 滑动窗口、限流计数器、分布式锁 |
| **向量库** | Qdrant | v1.12.1 | 长期记忆存储、BGE-M3 向量检索 |
| **Embedding** | BGE-M3 (Ollama) | — | 中文语义向量化、零成本 |
| **消息队列** | RabbitMQ | 3.x | 4 队列: memory/cleanup/events/default |
| **异步任务** | Celery + Beat | — | 后台记忆写入、偏好提取、事件消费 |
| **前端** | Next.js + React | 14 / 18 | SSE 流式渲染、Zustand 状态管理 |
| **日志** | structlog | — | JSON 结构化日志、LLM 用量追踪 |

## 3. 部署架构

8 个容器通过 `shopmesh` bridge 网络互联：

```yaml
# docker-compose.yml 核心服务
services:
  traefik:       # :80 外部入口, :8080 管理面板
  java-api:      # :8080 内部, 连接 postgres + rabbitmq
  python-agent:  # :9000 内部, 连接 postgres + redis + qdrant + rabbitmq
  celery-worker: # 消费 4 队列: memory, cleanup, default, events
  celery-beat:   # 定时调度: 每日清理过期记忆
  postgres:      # :5432, 16-alpine, named volume
  redis:         # :6379, 7-alpine
  qdrant:        # :6333, v1.12.1
  rabbitmq:      # :5672 AMQP, :15672 管理面板, 3-management-alpine
```

**关键设计**：`java-api` 和 `python-agent` 均不直接暴露端口到宿主机，所有外部流量必须经过 Traefik :80。

## 4. 请求入口层 — Traefik 网关

### 4.1 路由规则 (`traefik/dynamic.yml`)

| 路径前缀 | 目标服务 | 说明 |
|----------|---------|------|
| `/api/auth/*` | `java-api:8080` | 注册、登录、Token 刷新、用户信息 |
| `/api/users/*` | `java-api:8080` | 用户管理 |
| `/api/merchants/*` | `java-api:8080` | 商户管理 |
| `/api/products/*` | `java-api:8080` | 商品 CRUD |
| `/.well-known/jwks.json` | `java-api:8080` | RSA 公钥端点 |
| `/api/chat/*` | `python-agent:9000` | Agent 对话 |
| `/api/conversations/*` | `python-agent:9000` | 会话管理 |
| `/api/behavior/*` | `python-agent:9000` | 行为追踪 |
| `/api/tasks/*` | `python-agent:9000` | 后台任务状态 |

### 4.2 网关中间件

- **gateway-ratelimit**: `average: 500/s, burst: 1000`，基于 IP 策略 (`depth: 1`)
- **security-headers**: XSS 过滤、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、HSTS (31536000s)

## 5. 认证层 — JWT 跨语言信任

### 5.1 Java 控制面 (签发侧)

**核心类**: `com.shopmesh.auth.JwtProvider`

```
RSA-2048 密钥对
  ├── 启动时从 PEM 文件加载 (config/jwt-private.pem, config/jwt-public.pem)
  ├── 文件不存在则自动生成并写入磁盘
  └── 生成 kid = base64url(SHA-256(public_key_encoded)[:16])
```

**Token 签发**:
- Access Token: `RS256, sub=user_id, tenant_id=xxx, type=access, exp=30min`
- Refresh Token: `RS256, sub=user_id, tenant_id=xxx, type=refresh, exp=7d`

**Auth 端点** (`AuthController`, `/api/auth`):

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/auth/register` | 注册 → 返回 access + refresh token | 否 |
| POST | `/api/auth/login` | 登录 → 返回 access + refresh token | 否 |
| POST | `/api/auth/refresh` | 刷新 Token | 否 |
| GET | `/api/auth/me` | 当前用户信息 | 是 |

**API Key 端点** (`ApiKeyController`, `/api/auth/api-keys`):

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/api-keys` | 创建 API Key (`sk_live_...`) |
| GET | `/api/auth/api-keys` | 列出活跃 Key |
| DELETE | `/api/auth/api-keys/{keyId}` | 吊销 Key |

**JWKS 端点** (`JwksController`):
- `GET /.well-known/jwks.json` → 返回标准 JWKS JSON (`kty=RSA, use=sig, alg=RS256, kid=xxx, n=xxx, e=xxx`)

### 5.2 Python Agent 引擎 (验证侧)

**核心类**: `src/auth/jwks_client.py`

```
请求到达 Python
  → TenantMiddleware 提取 Authorization: Bearer <token>
  → jwks_client.validate_jwt(token)
      ├── get_public_key() → 从 http://java-api:8080/.well-known/jwks.json 拉取
      │   ├── 缓存 1 小时 (double-checked locking)
      │   ├── 拉取失败 → 使用过期缓存 (降级)
      │   └── JWK → RSA PublicKey 转换
      ├── RS256 验签 + exp 校验
      └── 回退: 无 JWKS → HS256 对称验证 (迁移期)
```

### 5.3 安全过滤链 (Java 侧)

```
Request
  → JwtAuthFilter (提取 Bearer → 验签 → 设置 SecurityContext + request attributes)
  → TenantFilter (request attributes → TenantContext ThreadLocal)
  → Controller
```

## 6. Python 中间件栈

执行顺序 (LIFO，即代码中后添加的先执行):

```
Request → CORSMiddleware → TenantMiddleware → RateLimitMiddleware → Handler
```

### 6.1 TenantMiddleware (`src/auth/middleware.py`)

从请求中提取身份，按优先级:
1. `Authorization: Bearer <token>` → `decode_token()` → 设置 `tenant_id`, `user_id` 到 contextvars
2. `X-API-Key` → `validate_api_key()` → 设置 `tenant_id` 到 contextvars
3. 均无 → contextvars 保持默认空值

### 6.2 RateLimitMiddleware (`src/ratelimit/middleware.py`)

三层滑动窗口限流，基于 Redis Sorted Set + Lua 原子脚本:

| 层级 | Redis Key | 默认阈值 | 窗口 |
|------|-----------|---------|------|
| per-user | `rl:user:<user_id>` | 60 req | 60s |
| per-tenant | `rl:tenant:<tenant_id>` | 300 req | 60s |
| global | `rl:global` | 1000 req | 60s |

**检查顺序**: per-user → per-tenant → global，首个拒绝即短路返回 429。
**降级策略**: Redis 不可用时 **fail open** (放行请求)。

**豁免路径**: `/api/health`, `/api/auth/login`, `/api/auth/register`

## 7. Agent 引擎 — LangGraph 全流程

### 7.1 执行路径

系统默认走 **Orchestrator DAG 路径** (`mode="multi_agent"`)：

```
preprocess → orchestrator → dag_executor → postprocess → END
```

也保留旧的 **Legacy Router 路径** (`mode="multi_agent_legacy"`)：

```
preprocess → agent_router → [search_recommend | detail_compare | __order__]
  → should_continue → (continue/end/fallback) → postprocess → END
```

### 7.2 预处理 (`src/graph/preprocessing.py` — `node_preprocess`)

6 个任务通过 `asyncio.gather` **并行执行**:

| 任务 | 函数 | 数据源 | 输出 |
|------|------|--------|------|
| 意图分类 | `classify_intent()` | Semantic Router (Qdrant) → LLM 回退 | `user_goals`, `task_type` |
| 实体抽取 | `extract_entities()` | LLM 结构化输出 | category, brand, price, scenario 等 |
| 向量记忆召回 | `recall()` | Qdrant `user_long_term_memories` 集合 | 历史偏好片段 |
| Session 窗口 | `get_window()` | Redis List (L2a, 滑动 5 轮) | 近期对话 |
| Session 摘要 | `get_summary()` | Redis String (L2b, LLM 压缩) | 历史摘要 |
| 用户画像 | `get_global_profile()` | PostgreSQL `user_profiles` (L3) | 品牌偏好、价格敏感度 |

**后续处理**:
- 上下文继承: 从上一轮继承缺失的实体字段 (category, brand, price_min/max)
- 任务切换检测: 不同 scenario/category/product_type 时清空上下文
- 软需求归一化 → 实体验证 → 消歧 → 搜索规划

**追问路径**: 若 state 中存在 `pending_clarification`，走追问回答路由 (澄清/全切换/部分切换)。

### 7.3 编排器 (`src/graph/orchestrator.py` — `node_orchestrator`)

```
输入: user_goals, entities
  │
  ├─ 尝试确定性映射: resolve_intent_tasks(user_goals)
  │   └─ 成功 → 直接输出 task DAG
  │
  └─ 失败 → LLM 分解
      ├── Prompt: 列出可用 task templates (白名单)
      ├── LLM 输出 JSON DAG (task_id + dependencies)
      └── 验证 DAG:
          ├── 未知 task_id? → 回退单任务
          ├── 环检测 (DFS)? → 回退单任务
          ├── 深度 > 3? → 回退单任务
          └── 任务数 > 8? → 回退单任务
```

### 7.4 DAG 执行器 (`src/graph/dag_executor.py` — `node_dag_executor`)

```
输入: task_dag (带依赖关系的任务图)
  │
  ├─ 拓扑排序 (Kahn 算法) → 分层
  │
  └─ 逐层并行执行 (asyncio.gather):
      ├── type:tool → _execute_tool_task() → 直接调用工具 (无 LLM)
      └── type:agent → _execute_agent_task() → 进入 ReAct 循环
          └─ 注入前序任务结果到上下文
  │
  └─ 合并所有任务结果 → merge_final_results()
```

### 7.5 Agent ReAct 循环 (`src/graph/specialized_agents.py` — `_run_agent_loop`)

```
输入: state + agent_name
  │
  ├─ 获取 agent config (tools, prompt, response_type)
  ├─ 构建 system prompt (agent 专属 prompt builder)
  ├─ 构建 messages (system + 历史 + 工具调用记录)
  │
  └─ 循环 (max_iterations 次):
      ├─ 调用 LLM (附带工具 schema)
      │
      ├─ 返回 tool_calls?
      │   ├─ 是 → execute_tool() → 结果追加 → 继续循环
      │   └─ 否 → 解析 Final Answer JSON
      │       ├─ schema 验证 → parse_response_json()
      │       ├─ 失败 → _attempt_repair() (LLM 修复)
      │       └─ 成功 → Output Guard 校验
      │           ├─ 幻觉检测: product_id 是否在搜索结果中
      │           ├─ 价格边界: price < historical_min * 0.3?
      │           └─ 覆盖率: 输出数 ≤ 搜索结果数?
      │
      └─ 提取 selected_product_ids + summary
```

**循环控制**:
- `end`: final_response 已设置
- `fallback`: 达到最大迭代 或 同工具+参数重复两次 (死循环检测)
- `continue`: 继续循环

### 7.6 后处理 (`src/graph/postprocessing.py` — `node_postprocess`)

```
输入: final_response + session context
  │
  ├─ Data Guard: PII 脱敏 (手机号/卡号/身份证)
  ├─ L2a: session_mem.add_turn() → Redis RPUSH (< 1ms, 同步)
  ├─ Celery: save_conversation_message.delay() → PostgreSQL 持久化
  ├─ Celery: trim_session.delay() → 超出窗口的轮次 LLM 压缩
  │
  └─ 偏好提取 (两级):
      ├─ 强信号 (正则匹配 "我喜欢XX"/"不要XX"):
      │   ├─ write_vector_memory.delay() → Qdrant L2c
      │   └─ update_profile_preference.delay() → PostgreSQL L3
      ├─ 噪音 ("好的"/"谢谢"/"这个"):
      │   └─ 跳过
      └─ 中间态:
          └─ Redis 计数器 +1 → 每 3 轮 → SETNX 锁 → batch_classify_preferences.delay()
```

## 8. 工具层 (`src/tools/`)

Agent 可调用的 7 个工具:

| 工具 | 功能 | 权限等级 |
|------|------|---------|
| `product_search` | 一站式检索: 硬筛 + 向量 + 排序 | READ |
| `multi_query_search` | 多查询并行检索 + 去重 + 重排 | READ |
| `product_detail_batch` | 批量获取商品详情 | READ |
| `price_compare` | 多商品比价 | READ |
| `review_summary` | 评论摘要 | READ |
| `constraint_relaxation` | 放宽检索约束 (搜索失败自纠) | READ |
| `ask_clarification` | 缺失槽位检查 + 追问 | READ |

工具通过 `ToolDef` 注册到 `tool_registry`，`execute_tool()` 是统一入口，执行前检查:
- **权限检查**: `check_permission(tool.permissions)`
- **速率限制**: 10 次/分钟/用户 (内存计数)
- **日志消毒**: `sanitize_for_log()` 对参数做 PII 脱敏

## 9. 记忆系统 — 四层五级

| 层级 | 存储 | 读取时机 | 写入时机 | 生命周期 |
|------|------|---------|---------|---------|
| **L1 Working** | 进程内存 (dict) | 每次执行 | 每次执行 | 单次请求 |
| **L2a Session** | Redis List | 预处理 `get_window()` | 后处理 `add_turn()` | 滑动 5 轮 |
| **L2b Summary** | Redis String | 预处理 `get_summary()` | Celery `trim_session` | 超出窗口时 LLM 压缩 |
| **L2c Vector** | Qdrant | 预处理 `recall()` | Celery `write_vector_memory` | 180 天衰减清理 |
| **L3 Profile** | PostgreSQL | 预处理 `get_global_profile()` | Celery `update_profile_preference` | 永久 |

**向量记忆细节**:
- 统一集合 `user_long_term_memories` + `user_id` payload 过滤 (避免 per-user 集合 OOM)
- BGE-M3 embedding (Ollama 本地, 零成本)
- 读时动态衰减: `score × importance × e^(-λt)`
- 矛盾检测: 新记忆写入前与已有记忆对比

**偏好提取策略**:
- 强信号 → 立即写入 (正则匹配)
- 噪音 → 跳过
- 中间态 → 每 3 轮批量 LLM 分类 (从 100% LLM 调用降到 ~33%)

## 10. 异步任务层 — Celery + RabbitMQ

### 10.1 队列分配

| 队列 | 任务 | 说明 |
|------|------|------|
| `memory` | trim_session, write_vector_memory, batch_classify_preferences, update_profile_preference, save_conversation_message, process_behavior_signal | 记忆和偏好操作 |
| `cleanup` | cleanup_expired_memories, cleanup_user_memories | 定期清理 |
| `events` | handle_user_registered, handle_user_profile_updated | Java → Python 事件消费 |
| `default` | (fallback) | 未路由的任务 |

### 10.2 Java → Python 事件流

```
Java (UserEventPublisher)
  → Exchange: shopmesh.events (DirectExchange)
  → Routing Key: user.registered / user.profile.updated
  → Queue: events (durable)
  → Python Celery Worker (event_tasks.py)
      ├─ handle_user_registered → 初始化 Qdrant 向量集合 + payload 索引
      └─ handle_user_profile_updated → 同步品牌偏好变更到 L3 Profile
```

**可靠性保证**: `task_acks_late=True`, `task_reject_on_worker_lost=True`, `max_retries=3`

### 10.3 Beat 定时调度

| 任务 | 周期 | 说明 |
|------|------|------|
| `cleanup_expired_memories` | 每 24 小时 | 清理 180 天未访问的向量记忆 |

## 11. 安全体系 — Guardrails 四层

### 11.1 Input Guard (`src/security/input_guard.py`)

在 `/api/chat` 入口处、Agent 执行前拦截:

| 检查 | 函数 | 规则 | 严重度 |
|------|------|------|--------|
| 长度 | `check_input_length()` | > 500 字符 | block |
| 注入检测 | `check_injection()` | 12 条中英文正则 (ignore instructions/jailbreak/DAN/ChatML token 等) | block |
| 意图白名单 | `check_intent_whitelist()` | 9 种合法意图 | warn |

### 11.2 Output Guard (`src/security/output_guard.py`)

在 Agent 最终输出后校验:

| 检查 | 函数 | 规则 | 严重度 |
|------|------|------|--------|
| 幻觉检测 | `check_item_traceability()` | 推荐的 product_id 必须在搜索结果中 | warn (不阻断) |
| 价格边界 | `check_price_boundary()` | price < historical_min × 0.3 | warn |
| 覆盖率 | `check_coverage()` | 输出数 ≤ 搜索结果数 | warn |

### 11.3 Permission Guard (`src/security/permission.py`)

工具调用前检查:

| 检查 | 规则 |
|------|------|
| 权限等级 | READ (自动) / WRITE (用户确认) / SENSITIVE (二次确认) |
| 速率限制 | 10 次/分钟/用户 (内存计数) |
| 金额一致性 | `abs(display_price - actual_price) > 0.01` |

### 11.4 Data Guard (`src/security/data_guard.py`)

全流程 PII 脱敏:

| 函数 | 应用位置 | 规则 |
|------|---------|------|
| `mask_phone()` | 后处理响应文本 | `138****1234` |
| `mask_card_number()` | 后处理响应文本 | `6222****1234` |
| `mask_id_number()` | 后处理响应文本 | `110101****1234` |
| `sanitize_for_log()` | 工具执行日志 | 敏感字段替换为 `[REDACTED]` |

### 11.5 三层限流总结

| 层级 | 位置 | 实现 | 阈值 |
|------|------|------|------|
| 网关层 | Traefik | 内置 rateLimit | 500/s, burst 1000 |
| 应用层 | Python Middleware | Redis Sorted Set + Lua | 60/user, 300/tenant, 1000/global (60s) |
| 工具层 | Permission Guard | 内存计数器 | 10/min/user |

## 12. 可观测性

### 12.1 structlog JSON 日志

所有关键事件以结构化 JSON 写入 `logs/app.jsonl`:
- `llm_usage`: model, input_tokens, output_tokens, cost_cents, tenant_id
- `tool_call`: tool_name, args (脱敏后), duration_ms
- `daily_budget_exceeded`: tenant_id, cost_cents
- Agent 执行轨迹: intent, entities, task_dag, final_response

### 12.2 LLM 成本追踪 (`src/observability/cost_tracker.py`)

```
LLMClient.chat() / chat_stream()
  → 捕获 usage (prompt_tokens + completion_tokens)
  → record_usage()
      ├── structlog JSON 日志
      ├── 内存累加器 (per-tenant per-day)
      │   └─ 超阈值 → structlog warning (daily_budget_exceeded)
      └── PostgreSQL llm_usage 表 (best-effort, DB 不可用则静默跳过)
```

**查询接口**: `GET /api/admin/costs?tenant_id=xxx&days=7` → 返回 today + history + pricing

**定价配置** (DeepSeek):
- Input: $0.15 / 1M tokens
- Output: $0.60 / 1M tokens
- 日预算告警: $100 / tenant / day

### 12.3 规划中 (Phase 6)

- Prometheus + Grafana 指标采集
- Agent 全链路 Trace

## 13. 流式输出 — SSE 端到端

### 13.1 后端 SSE 事件类型

| 事件 | 触发时机 | 数据 |
|------|---------|------|
| `intent` | 预处理完成 | user_goals, task_type |
| `entities` | 预处理完成 | category, brand, price 等 |
| `status` | 各节点进度 | "thinking...", "searching..." |
| `tool_call` | 工具调用完成 | tool_name, result |
| `task_dag` | 编排器完成 | 任务依赖图 |
| `card_preload` | 流式输出开始 | 全部商品数据 (前端缓存) |
| `product_intro_start` | 单商品介绍开始 | product_id |
| `text_delta` | LLM 逐 token 生成 | 文本片段 |
| `product_card` | 商品卡片就绪 | 商品详情 |
| `product_intro_done` | 单商品介绍结束 | — |
| `summary_start` | 总结开始 | — |
| `summary_delta` | 总结逐 token | 文本片段 |
| `clarification` | 需要追问 | 问题 + 选项 |
| `interrupt` | HITL 订单确认 | 订单详情 |
| `error` | 异常 | 错误信息 |

### 13.2 流式输出架构 (`src/graph/stream_utils.py`)

```
Graph 执行完成
  │
  ├─ stream_narrative() — 逐商品 LLM 流式生成:
  │   ├─ card_preload → 前端缓存全部商品数据
  │   ├─ 逐个商品 (间隔 0.3s):
  │   │   ├─ product_intro_start
  │   │   ├─ text_delta (逐 token, LLM.chat_stream())
  │   │   ├─ product_card (显示卡片)
  │   │   └─ product_intro_done
  │   └─ summary_start → summary_delta (总结)
  │
  └─ 回退: stream_explanation() — 整体解释流式输出
```

### 13.3 前端消费 (`frontend/src/hooks/useChatStream.ts`)

```
sendMessage(text)
  → POST /api/chat {message, session_id, user_id}
  → ReadableStream reader.read() 循环
  → 解析 SSE event: / data: 行
  → handleSSEEvent(eventType, data)
      ├─ status → 显示状态文字
      ├─ card_preload → 缓存商品数据
      ├─ text_delta → 逐字渲染
      ├─ product_card → 渲染商品卡片
      ├─ summary_delta → 渲染总结
      ├─ clarification → 渲染追问表单
      ├─ interrupt → 显示订单确认 (HITL)
      └─ error → 显示错误
```

## 14. 数据存储

### 14.1 PostgreSQL — 9 张表 (Flyway 管理)

| 表名 | 租户隔离 | 用途 |
|------|---------|------|
| `users` | ✅ tenant_id | 用户认证 |
| `api_keys` | ✅ tenant_id | 商户 API Key |
| `products` | ❌ | 商品目录 (共享) |
| `user_profiles` | ✅ tenant_id | 用户偏好画像 |
| `sessions` | ✅ tenant_id | 对话会话 |
| `orders` | ✅ tenant_id | 订单 |
| `intent_samples` | ❌ | NLU 训练样本 (共享) |
| `conversation_messages` | ✅ tenant_id | 对话历史 |
| `llm_usage` | ✅ tenant_id | LLM 用量追踪 |

**Schema 管理权**: Flyway (Java 侧) 是唯一的 schema owner，Alembic 已删除。

### 14.2 Redis — 三类用途

| 用途 | 数据结构 | Key 模式 |
|------|---------|---------|
| Session 滑动窗口 | List | session memory |
| 三层限流 | Sorted Set + Lua | `rl:user:*`, `rl:tenant:*`, `rl:global` |
| 分布式锁 | SETNX | 偏好提取锁 |

### 14.3 Qdrant — 向量记忆

- 集合: `user_long_term_memories`
- 索引: BGE-M3 (Ollama 本地)
- 过滤: `user_id` payload 过滤 (非 per-user 集合)
- 衰减: 读时 `score × importance × e^(-λt)`
- 清理: Beat 每日清理 180 天未访问的记忆

## 15. 端到端请求链路 — 完整时序图

```
用户: "帮我找一款300元以内的保湿面霜"
  │
  ▼
┌─ Traefik :80 ─────────────────────────────────────────────────────┐
│  PathPrefix(/api/chat) → python-agent:9000                        │
│  中间件: gateway-ratelimit (500/s) + security-headers              │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ Python 中间件栈 ──────────────────────────────────────────────────┐
│  CORSMiddleware → TenantMiddleware (JWT验签 via JWKS)              │
│  → RateLimitMiddleware (Redis 三层: 60/user, 300/tenant, 1000/g)  │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ /api/chat handler ───────────────────────────────────────────────┐
│  Input Guard: 长度 ✓ + 注入检测 ✓ + 意图白名单 ✓                   │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ preprocess (并行 6 任务) ─────────────────────────────────────────┐
│  classify_intent → ["find_product"] (Semantic Router, 0.92)       │
│  extract_entities → {category:"面霜", price_max:300, ...}         │
│  recall → [历史: 用户偏好清爽质地]                                  │
│  get_window → [上轮: 用户问过护肤品]                                │
│  get_summary → [用户关注保湿功效]                                   │
│  get_global_profile → {price_sensitivity: "medium", ...}          │
│  → 上下文继承 + 消歧 + 搜索规划                                    │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ orchestrator ────────────────────────────────────────────────────┐
│  确定性映射: find_product → task:search_product                    │
│  输出 DAG: [{id:"search", type:"tool", tool:"product_search"}]    │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ dag_executor ────────────────────────────────────────────────────┐
│  拓扑排序 → 1 层                                                  │
│  并行执行: product_search(面霜, price_max=300, ...)               │
│  → 返回 20 条搜索结果                                              │
│  合并结果 → selected_product_ids + search_results                  │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ postprocess ─────────────────────────────────────────────────────┐
│  Data Guard: PII 脱敏                                             │
│  L2a: Redis RPUSH (同步, <1ms)                                    │
│  Celery: save_conversation_message → PostgreSQL                   │
│  偏好提取: 无强信号, 噪音检测 → 跳过                               │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ stream_narrative ────────────────────────────────────────────────┐
│  SSE: card_preload (20 条商品数据)                                 │
│  SSE: product_intro_start → text_delta × N → product_card         │
│  SSE: product_intro_start → text_delta × N → product_card         │
│  ... (逐商品, 间隔 0.3s)                                          │
│  SSE: summary_start → summary_delta × N                           │
└───────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─ 前端渲染 ────────────────────────────────────────────────────────┐
│  handleSSEEvent → React state 更新 → ChatBox 渐进渲染             │
│  文字逐字出现 → 商品卡片依次展示 → 总结文本                         │
└───────────────────────────────────────────────────────────────────┘
```

## 16. 架构设计决策总结

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 整体架构 | Java 控制面 + Python Agent 引擎 | Java 处理 Auth/CRUD 成熟稳定; Python Agent 生态丰富 |
| 跨语言认证 | RSA 非对称 + JWKS | 企业级微服务标准实践; Python 不持有私钥 |
| 网关 | Traefik 路径级分流 | 请求直达目标服务, 无代理开销; 自动服务发现 |
| Agent 编排 | LangGraph DAG | 成熟稳定; 支持并行执行; 降低开发风险 |
| 预处理 | 确定性并行 | 意图分类/实体抽取是低风险强结构化任务, 确定性更稳定 |
| Agent 决策 | ReAct 动态循环 | 检索策略/追问/约束放宽等不确定决策需要动态推理 |
| 工具粒度 | 高层聚合 Tool | 减少 Agent 循环轮次, 降低延迟 |
| 记忆更新 | 确定性后处理 (非 Agent 工具) | 防止临时需求被误写为长期偏好 |
| 向量库 | Qdrant (非 FAISS) | 原生 Payload 预过滤, 避免 FAISS 后过滤的漏斗陷阱 |
| 异步任务 | Celery + RabbitMQ (非 Redis broker) | RabbitMQ 持久化 + 死信队列, 生产级可靠性 |
| 限流 | 三层 (网关+应用+工具) | 纵深防御, 各层独立降级 |
| 流式输出 | SSE (非 WebSocket) | 单向服务端推送足够; 实现简单; 浏览器原生支持 |
| Schema 管理 | Flyway (Java 侧唯一 owner) | 避免双 ORM 管理同一 schema 的冲突 |
