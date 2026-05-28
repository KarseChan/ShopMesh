# ShopMesh SaaS 技术路线图

## 目标

将 ShoppingAgent 从单用户 demo 演进为**多租户电商导购 SaaS 平台**，重点补充后端工程能力和 Agent 生产化技术栈。

## 核心原则

- 每个阶段产出**可运行的系统** + **简历可写的技术点**
- 本地 Docker 优先，后期可迁云
- 用 Claude 辅助开发，快速迭代
- 不堆功能，每个技术点都要**真正理解**

---

## Phase 1：认证鉴权 + 多租户隔离

**目标**：从"单用户 demo"变成"多商家隔离的 SaaS"

### 学什么
- JWT 认证流程（access_token + refresh_token）
- OAuth2 密码模式（FastAPI 内置支持）
- 多租户数据隔离模式（shared DB + tenant_id vs schema-per-tenant）
- FastAPI 依赖注入（Depends）做租户上下文传播
- Alembic 数据库迁移管理

### 做什么
1. 新增 `src/auth/` 模块：
   - JWT token 生成/验证（PyJWT）
   - 用户注册/登录 API（`POST /api/auth/register`, `POST /api/auth/login`）
   - 密码哈希（bcrypt via passlib）
   - `get_current_user` FastAPI 依赖
2. 多租户改造：
   - 所有表加 `tenant_id` 字段（商品、对话、记忆、用户画像）
   - `TenantContext` 中间件：从 JWT 提取 tenant_id，注入请求上下文
   - Qdrant 检索加 tenant_id payload filter
   - Redis key 加 tenant_id 前缀
3. 数据库迁移：
   - 引入 Alembic，管理 schema 版本
   - 初始迁移脚本
4. Docker Compose：
   - postgres + redis + qdrant + app 四个服务（Phase 2 加入 rabbitmq）
   - 一键 `docker compose up` 启动完整环境

### 简历技术点
- FastAPI + JWT + OAuth2 认证鉴权体系
- 多租户数据隔离（shared DB + row-level security）
- Alembic 数据库迁移管理
- Docker Compose 本地开发环境

---

## Phase 2：消息队列 + 异步任务

**目标**：把 fire-and-forget 的后台任务变成可靠执行

### 学什么
- 消息队列模式（生产者-消费者、延迟队列、死信队列）
- RabbitMQ 的 Exchange/Queue/Binding 模型
- Celery + RabbitMQ broker 的任务调度
- 任务重试、超时、幂等性
- 后台定时任务（Celery Beat）

### 做什么
1. 引入 Celery + RabbitMQ：
   - RabbitMQ 作为 broker（消息持久化、confirm 模式、死信队列）
   - Redis 作为 result backend（任务结果轻量，Redis 够用）
   - 新增 `src/tasks/` 模块
2. 迁移异步任务到 Celery：
   - 记忆压缩（L2b LLM summary）→ Celery task
   - 偏好分类（L2c batch classification）→ Celery task
   - 用户画像更新（L3 profile）→ Celery task
   - 记忆衰减清理（memory_decay）→ Celery Beat 定时任务
3. 任务可靠性：
   - 自动重试（max_retries=3, exponential backoff）
   - 超时控制（soft_time_limit）
   - RabbitMQ 死信队列（DLX：消息被 nack 或过期后自动路由到死信 exchange）
   - 消息确认（Celery acks_late + RabbitMQ publisher confirm）
4. 新增 `GET /api/tasks/{task_id}` 查询任务状态

### 简历技术点
- Celery + RabbitMQ 消息队列架构（Exchange/Queue/Binding 模型）
- 异步任务可靠性（重试、超时、死信队列 DLX）
- Celery Beat 定时任务调度
- 后台任务与请求解耦

---

## Phase 3：分布式限流 + API 网关

**目标**：从内存限流升级为分布式限流，引入 API 网关

### 学什么
- 分布式限流算法（令牌桶、滑动窗口）
- Redis Lua 脚本实现原子限流
- API 网关概念（Kong/Traefik）
- 速率限制策略（per-user, per-tenant, per-endpoint）

### 做什么
1. 分布式限流：
   - Redis sliding window 替代内存 dict
   - Lua 脚本保证原子性（INCR + EXPIRE）
   - 三级限流：per-user（10/min）、per-tenant（1000/min）、global（10000/min）
   - 返回 `429 Too Many Requests` + `Retry-After` header
2. API 网关（Traefik）：
   - Docker Compose 集成 Traefik
   - 自动服务发现
   - 路径前缀路由（`/api/*` → backend）
   - 速率限制中间件
3. API Key 管理：
   - 商家 API Key 生成/吊销
   - `X-API-Key` header 认证（除了 JWT 之外的第二种认证方式）
   - API Key 绑定 tenant_id

### 简历技术点
- Redis Lua 脚本实现分布式限流
- 三级速率限制策略
- Traefik API 网关 + 自动服务发现
- API Key 管理体系

---

## Phase 4：Agent 生产化 — Guardrails + 成本追踪

**目标**：把已有的安全模块真正接入，新增 LLM 成本追踪

### 学什么
- Agent Guardrails 设计模式（input/output/human-in-the-loop）
- LLM 成本管理（token 计费、预算控制）
- 结构化输出的可靠性保障

### 做什么
1. 接入已有 Guardrails：
   - `output_guard.validate_output` → 接入 `specialized_agents.py` 的 final answer 路径
   - `permission_guard.check_permission` → 接入 `tool_executor.py`
   - `data_guard` 自动脱敏 → 接入日志和响应
2. LLM 成本追踪：
   - 新增 `src/observability/cost_tracker.py`
   - 记录每次 LLM 调用的 input_tokens、output_tokens、model、cost
   - 按 tenant_id 聚合，存入 PostgreSQL
   - 新增 `GET /api/admin/costs` 查询接口
   - 预算告警：单日消费超阈值 → structlog warning
3. 结构化输出可靠性：
   - 统一 `_parse_final_answer` 的重试逻辑
   - schema 校验失败 → repair prompt → 最多重试 2 次
   - 记录 schema 修复成功率

### 简历技术点
- Agent Guardrails 体系（input/output/permission/data 四层防护）
- LLM token 成本追踪与预算控制
- 结构化输出可靠性保障（schema 校验 + 自动修复）

---

## Phase 5：安全体系 — RBAC + Prompt 安全 + 审计

**目标**：构建完整的 SaaS 安全防线，覆盖访问控制、Agent 安全、操作审计

### 学什么
- RBAC 角色权限模型（Role-Based Access Control）
- Prompt 注入防御（商家自定义 prompt 的安全边界）
- 订阅套餐与资源配额模型
- 审计日志设计（who did what when）
- 数据访问控制（防跨租户泄露）

### 做什么
1. RBAC 角色权限体系：
   - 四种角色：platform_admin / merchant_admin / merchant_staff / end_user
   - 权限表：角色 → 权限集合（chat:read, merchant:read, merchant:write, analytics:read, agent:config, admin:*）
   - `require_permission("merchant:write")` FastAPI 依赖装饰器
   - 角色绑定 tenant_id（商家管理员只能管自己的租户）
   - 新增 `src/auth/rbac.py`
2. 工具调用权限（套餐分级）：
   - 订阅套餐定义可用工具集：
     - free：product_search, ask_clarification
     - pro：+ multi_query_search, constraint_relaxation, review_summary
     - enterprise：全部工具 + 自定义 prompt
   - `tool_executor.py` 执行前检查 `tenant.plan → allowed_tools`
   - 超出套餐的工具调用返回 `403 Tool not available in your plan`
   - 配额管理：每月调用次数限制（free: 1000次/月, pro: 50000次/月）
3. Prompt 安全体系：
   - 商家 prompt 模板化：只允许 `{{变量}}` 占位符，不允许任意代码注入
   - 系统指令保护：系统 prompt 始终 prepend 在商家 prompt 之前，商家无法覆盖
   - 商家 prompt 输入经过 input_guard 扫描后再注入模板
   - prompt 变更审批：商家修改 prompt 后需 platform_admin 审核才生效（enterprise 套餐可自助）
   - 新增 `src/security/prompt_guard.py`
4. 审计日志：
   - 新增 `src/security/audit.py`
   - 记录：timestamp, tenant_id, user_id, action, resource, detail, ip_address
   - 覆盖动作：chat, tool_call, config_change, data_access, admin_action
   - 存入 PostgreSQL（audit_log 表）
   - 新增 `GET /api/admin/audit` 查询接口（platform_admin 专属）
   - 敏感操作实时告警（如 prompt 变更、权限修改）
5. 数据访问控制：
   - 所有查询自动加 `WHERE tenant_id = :current_tenant`，防跨租户泄露
   - input_guard 增强：per-tenant 自定义敏感词过滤规则

### 简历技术点
- RBAC 角色权限模型（四角色 × 多权限粒度）
- 工具调用权限 + 套餐分级 + 调用配额的资源管控模型
- Prompt 安全体系（模板化、注入防御、变更审批）
- 审计日志（全操作覆盖 + 敏感操作告警）
- 多租户数据访问控制（防跨租户泄露）

---

## Phase 6：评测体系 + 可观测性

**目标**：能衡量 Agent 好不好，能看到 Agent 在干什么

### 学什么
- LLM 应用评测框架（RAGAS / custom eval）
- Agent 可观测性（trace、replay、debug）
- Prometheus 指标采集 + Grafana 可视化
- 结构化日志查询（日志聚合）

### 做什么
1. 评测框架：
   - 新增 `evals/` 目录
   - 评测数据集：50 条标注 query + expected intent/entities/results
   - 自动化评测脚本：intent 准确率、entity F1、推荐召回率、端到端延迟
   - CI 集成：每次 PR 跑评测，回归检测
2. Agent Trace：
   - 给每次请求生成 trace_id
   - 记录：preprocess → agent_router → tool_calls → final_answer 全链路
   - 新增 `GET /api/admin/traces/{trace_id}` 查询完整 trace
   - 支持 replay：从 trace 复现请求
3. Prometheus + Grafana：
   - 指标：请求量、延迟 P50/P95/P99、LLM 调用次数、token 用量、错误率
   - Grafana dashboard：实时监控面板
   - Docker Compose 集成 prometheus + grafana
4. 日志聚合：
   - structlog JSON 日志 → 文件 → 简单的查询脚本
   - 按 request_id 串联完整请求链路

### 简历技术点
- LLM 应用评测框架（自动化 benchmark + CI 集成）
- Agent 全链路 Trace（可观测性）
- Prometheus + Grafana 监控体系
- 结构化日志聚合与查询

---

## Phase 7：支付集成 + 商家后台 API

**目标**：完成导购闭环——从推荐到下单到支付

### 学什么
- 第三方支付集成（微信/支付宝沙箱）
- Webhook 回调处理（签名验证、幂等、重试）
- 商家 CRUD API 设计
- 幂等性设计（幂等 key）

### 做什么
1. 支付集成（沙箱环境）：
   - 微信支付 V3 API（沙箱）
   - 统一下单 → 支付链接 → 回调通知
   - 签名验证（RSA）
   - 订单状态机：created → paid → shipped → completed
2. Webhook 可靠性：
   - 签名验证（防伪造）
   - 幂等处理（同一事件不重复处理）
   - 重试机制（支付平台会重试回调）
   - 死信队列（处理失败的回调）
3. 商家后台 API：
   - `POST /api/merchant/products` 商品 CRUD
   - `GET /api/merchant/analytics` 推荐统计
   - `PUT /api/merchant/config` Agent 配置（prompt 定制、工具开关）
4. 订单管理：
   - 订单表（PostgreSQL）
   - 订单状态流转
   - 关联推荐记录（哪个 Agent 推荐了什么 → 用户买了什么）

### 简历技术点
- 微信支付 V3 集成（签名验证、回调处理）
- Webhook 可靠性设计（幂等、重试、死信）
- 商家后台 RESTful API 设计
- 订单状态机

---

## Phase 8：容器化部署 + CI/CD

**目标**：从本地开发到可部署的生产环境

### 学什么
- Docker 多阶段构建（减小镜像体积）
- GitHub Actions CI/CD pipeline
- 环境管理（dev/staging/prod）
- 数据库备份策略

### 做什么
1. Docker 优化：
   - 多阶段构建（builder → runtime）
   - 镜像体积优化（从 ~2GB 到 ~500MB）
   - healthcheck 配置
2. CI/CD（GitHub Actions）：
   - PR → lint + type check + tests + evals
   - merge to main → build image → push to registry
   - staging 部署 → smoke test
3. 环境管理：
   - `.env.dev`, `.env.staging`, `.env.prod`
   - config.yaml 环境变量覆盖
   - 数据库迁移自动化（alembic upgrade in entrypoint）
4. 生产配置：
   - Uvicorn workers 配置
   - PostgreSQL 连接池
   - Redis 连接池
   - 日志文件轮转

### 简历技术点
- Docker 多阶段构建 + 镜像优化
- GitHub Actions CI/CD pipeline
- 多环境配置管理
- 生产级部署配置

---

## 技术栈总览（完成后）

### 后端（新增）
| 技术 | 用途 | Phase |
|------|------|-------|
| JWT + OAuth2 | 认证鉴权 | 1 |
| Alembic | 数据库迁移 | 1 |
| Celery + RabbitMQ | 消息队列 | 2 |
| RabbitMQ Management UI | 队列监控 | 2 |
| Redis Lua | 分布式限流 | 3 |
| Traefik | API 网关 | 3 |
| RBAC | 角色权限 | 5 |
| 微信支付 V3 | 支付集成 | 7 |
| Prometheus | 指标采集 | 6 |
| Grafana | 监控可视化 | 6 |
| GitHub Actions | CI/CD | 8 |
| Docker 多阶段构建 | 容器化 | 8 |

### Agent（新增/增强）
| 技术 | 用途 | Phase |
|------|------|-------|
| 多租户记忆隔离 | 10K+ 用户 | 1 |
| Guardrails 四层防护 | 生产安全 | 4 |
| LLM 成本追踪 | token 计费 | 4 |
| 结构化输出可靠性 | schema + repair | 4 |
| 工具权限 + 配额 | 套餐分级管控 | 5 |
| Prompt 安全体系 | 模板化 + 注入防御 + 变更审批 | 5 |
| 审计日志 | 全操作覆盖 + 告警 | 5 |
| 数据访问控制 | 防跨租户泄露 | 5 |
| 评测框架 | 质量保障 | 6 |
| Agent Trace | 可观测性 | 6 |
| Webhook 可靠性 | 支付回调 | 7 |

---

## 简历最终效果

```
ShopMesh 电商导购 SaaS 平台 | Python / FastAPI / LangGraph / PostgreSQL / Redis / RabbitMQ / Docker

• 基于 LangGraph 构建多 Agent 导购系统，支持推荐/搜索/详情/对比四种场景，
  Orchestrator DAG 编排复合意图
• 实现 4 层记忆架构（会话窗口/向量记忆/用户画像/知识库），支持 10K+ 租户隔离
• 设计 JWT + OAuth2 认证鉴权体系，多租户数据隔离（shared DB + row-level security）
• 集成 Celery + RabbitMQ 消息队列处理异步任务（记忆压缩、画像更新），DLX 死信队列保障消息可靠性
• Redis Lua 脚本实现三级分布式限流，Traefik API 网关自动服务发现
• 构建 Agent Guardrails 四层防护（输入/输出/权限/数据），LLM token 成本追踪与预算控制
• 设计完整安全体系：RBAC 四角色权限、工具调用权限 + 套餐配额、Prompt 模板化 + 注入防御 + 变更审批、审计日志全操作覆盖
• 搭建评测框架（50 条标注数据，intent/entity/recall/latency 四维指标）+ CI 自动回归
• Prometheus + Grafana 监控体系，Agent 全链路 Trace 可观测性
• 微信支付 V3 沙箱集成，Webhook 幂等/重试/死信可靠性设计
• Docker 多阶段构建 + GitHub Actions CI/CD，多环境配置管理
```
