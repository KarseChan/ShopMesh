# ShopMesh

基于 LangGraph 的对话式导购 + 交易 Agent：自然语言 → 意图路由 → 每意图独立 ReAct agent（工具：混合检索 / 多目标排序 / 购物车 / 下单）→ 推荐与购买闭环。

> **这个项目的重点不是功能多，而是工程判断**：我对它做的架构收敛、延迟优化、生产级 agent 交易设计，以及对早期过度设计的诚实反思。功能清单在最后，工程亮点在最前。

---

## 工程亮点

> 这一节是项目真正有分量的部分 —— 不是功能清单，而是**工程判断与可量化的改进**。

### 1. 架构收敛：主动砍掉不需要的复杂度

早期仓库并存 **5 套执行编排**（workflow / multi_agent / orchestrator / dag_executor / dag_engine）。我删除死代码、**收敛为单一主路径**：确定性意图路由 → 每意图一个 ReAct agent（独立 prompt / 工具子集 / 迭代预算）。

评估后主动砍掉「LLM 动态拆解任务 DAG」——导购意图空间是**封闭已知**的（搜/推、详情/比价、澄清），确定性路由才是正解；DAG 换来的灵活性用不上，却多一次 LLM 调用 + JSON 解析失败点 + 环检测，还 0 测试、有过循环 bug。**识别并移除过度设计，比堆功能更能说明判断力。**

### 2. 端到端延迟优化 116s → 22–39s

先用 profiler **实测定位**：116s ≈ 8 次串行 LLM（抽实体 20s + agent 迭代 + 逐商品文案 47s + 总结 14s）。根因是网关只有一个慢推理模型（单次 7–15s）、**换不了模型 → 只能减少调用次数**。对照生产级做法（LLM 离线富化 + 在线毫秒级召回），把能确定性化/预计算的都移出在线路径：

- 逐商品推荐文案改用排序器已算好的确定性理由（去 3 次 LLM）
- 收尾总结模板化（去 1 次 LLM）
- 简单首轮 query 用规则/关键词抽实体，跳过抽取 LLM（preprocess 20s → 2.6s）
- 收敛 agent 冗余工具调用 + 去重守卫

### 3. 生产级 Agent 交易设计：不让 LLM 自主花钱

核心判断：**LLM 会幻觉、会被 prompt injection，绝不让它自主执行支付**。分层授权：读（搜索）=agent 自由；写（购物车，可撤销）=agent 可调；敏感（下单/支付）=**必须人工确认 + 确定性代码执行**，SENSITIVE 工具在执行器被拦截。工程细节：

- 库存 Redis **原子预占**防超卖
- 下单**幂等键**防重复扣款
- 支付**只信 HMAC 验签 webhook**（伪造回调实测被拒）
- Celery 定时**对账取消**超时订单并释放库存
- 支付不接真实资金，用沙箱走与真实一致的验签链路

### 4. Eval 驱动，用数据而非「感觉」改进

`scripts/eval_recommendation.py` 用可复现指标量化推荐质量（非空/预算/品类/product_type/场景命中率）。首跑 72.4% 定位到两类缺陷并闭环修复，**升至 96.6%**：

- **无解预算**（如「面霜 ≤¥300」而最便宜 ¥574）：`product_search` 空结果时**确定性放宽**约束（护住 product_type/category）+ 给最接近的替代 + 用户可见放宽提示，而非返回空。
- **礼物场景无锚点召回打空**：把场景品类白名单**下推到 Qdrant 预过滤**，让 HNSW 只遍历合适品类，而非事后过滤把结果清空。

并新增「优雅降级」指标：无解约束时以带标注的放宽结果替代空结果。

### 5. 基础设施排障 & 收尾

定位并修复一批隐藏问题：`BaseHTTPMiddleware` 缓冲导致 SSE 流式失效（改纯 ASGI）、`localhost` 解析到 IPv6 空 Ollama 实例、会话历史因 `_try_db` 误判被静默丢失、Celery 任务从未注册等。工程收尾：静默异常治理（吞异常改为可观测）、**收敛双迁移为单一 source of truth**（Flyway 唯一，删除并行的 Alembic，详见 [docs/MIGRATIONS.md](docs/MIGRATIONS.md)）。

---

## 过度设计反思（诚实的部分）

一个秋招项目容易堆技术栈显得「高大上」。这里主动标出对该规模其实偏重的选择：

- **双语言微服务（Java 控制面 + Python Agent）/ RabbitMQ 4 队列 / Traefik 网关** —— 对当前业务规模是过度设计。如果重来，我会**先用单语言 + 直连**，等真有多团队协作或异步解耦需求再拆。保留它们是为了练习完整链路（跨语言 JWT 信任、消息队列、网关分流），但我清楚这是学习成本、不是业务必需。
- 早期移植自 Claude Code 的一批「优化」（hooks / context compact / error recovery），我在梳理时发现 **hooks 系统其实从未接线、是空转的** —— 没把它当「已完成功能」讲。关键不是移植了多少，而是理解每一项**为什么存在**、判断哪些适用。

**天花板**：这是「把已知模式落地成有生产意识的 agent 系统」，不是「提出新架构」。

---

## 架构

**双语言微服务**：Java 控制面（Auth / CRUD）+ Python Agent 引擎（LangGraph / Tools / Memory），Traefik 按路径分流，RabbitMQ 异步通信。

```
Client → Traefik (:80)
  ├── /api/auth/*, /.well-known/jwks.json          → Java  :18080  （注册/登录/JWT 签发/API Key）
  └── /api/chat/*, /api/conversations/*, ...        → Python :9000   （LangGraph Agent / 工具 / 记忆）
```

- **跨语言信任**：Java 用 RSA 签发 RS256 JWT，Python 通过 JWKS 端点拉公钥验证并缓存（Python 不签发 JWT）。
- **Python Agent Pipeline**：`preprocess`（意图+实体+记忆并行）→ 确定性 `agent_router` → `search_recommend` / `detail_compare` agent（ReAct loop）→ `postprocess`（记忆更新）。
- **四层记忆**：进程内工作记忆 / Redis 会话滑窗 / Qdrant 向量长期记忆（读时衰减）/ PostgreSQL 用户画像。
- **Schema**：**Flyway 是唯一 source of truth**（Java 启动自动迁移），Python 只经 SQLModel 读写、不写迁移。见 [docs/MIGRATIONS.md](docs/MIGRATIONS.md)。

### 技术栈

| 层 | 技术 |
|---|------|
| 控制面 | Java 17 / Spring Boot 3.4 / Spring Security / JPA / Flyway / jjwt (RSA-256) |
| Agent 引擎 | Python 3.11+ / LangGraph / FastAPI / httpx / Celery |
| LLM | OpenAI 兼容网关（单一慢推理模型，不可换 → 优化方向是减少调用次数） |
| Embedding | BGE-M3 via Ollama |
| 向量库 / 关系库 / 缓存 | Qdrant / PostgreSQL 16 / Redis 7 |
| 消息队列 / 网关 | RabbitMQ / Traefik v3 |
| 前端 | Next.js 14 / React 18 / Tailwind / Zustand |
| 日志 | structlog (JSON) |

### 项目结构

```
ShopMesh/
├── src/                    # Python Agent 引擎
│   ├── api/                # FastAPI 路由（SSE 流式）
│   ├── agents/             # 意图分类 / 实体提取 / 排序 / 解释
│   ├── graph/              # LangGraph 状态图与编排（单一主路径）
│   ├── retrieval/          # 混合检索（向量 + 结构化过滤）
│   ├── tools/              # Agent 工具（检索/比价/详情/放宽/追问）
│   ├── skills/             # 交易 skill（购物车/下单/支付）
│   ├── memory/             # 四层记忆与上下文压缩
│   ├── security/           # 输入输出防护 + 权限 + PII 脱敏
│   ├── tasks/              # Celery 后台任务（4 队列）
│   └── ...                 # router / resilience / observability / db
├── shopmesh-java/          # Java 控制面（Auth / API Key / JWKS / Flyway 迁移）
├── frontend/               # Next.js 前端
├── traefik/                # 网关配置
├── scripts/                # build_index / eval / mock 数据生成
├── data/                   # mock_products_5k.json (5000) + intent_samples
├── tests/                  # 回归/集成测试
├── docker-compose.yml      # 完整栈（9 服务）
└── docker-compose.infra.yml# 仅依赖容器（本地开发用）
```

---

## 快速开始

### 方式 A：完整栈（一条命令）

```bash
docker compose up          # 9 服务：traefik / java-api / python-agent / celery(worker+beat) / postgres / redis / qdrant / rabbitmq
```

网关在 `:80`，前端另起（见下）。首次需构建向量索引（见方式 B 的 build_index 步骤）。

### 方式 B：本地开发（依赖容器 + 手动起服务）

```bash
# 1. 依赖容器（postgres:15432 / redis:16379 / qdrant:16336 / rabbitmq:5672 / ollama:11434）
docker compose -f docker-compose.infra.yml up -d

# 2. Python 环境
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env        # 填入 LLM_API_KEY

# 3. 构建向量索引（需 Qdrant + Ollama bge-m3）
python scripts/build_index.py

# 4. 后端 API（:9000）
PYTHONPATH=. uvicorn src.api.chat:app --reload --port 9000

# 5. Celery worker（订单超时取消/记忆任务需要；Windows 用 --pool=solo）
celery -A src.tasks worker -Q memory,cleanup,default,events --pool=solo

# 6. Java 控制面（:18080，提供 Auth/JWKS）
cd shopmesh-java && mvn clean package -DskipTests && java -jar target/shopmesh-api-0.1.0.jar

# 7. 前端（:3000）
cd frontend && npm install && npm run dev
```

前端 `http://localhost:3000`，登录 `demo / demo1234`。

### 或直接用 CLI（免前端）

```bash
python main.py "帮我找护肤品"            # 单次
python main.py --stream "帮我找护肤品"   # 流式
```

---

## 可以尝试的提问

| 类型 | 示例 |
|------|------|
| 搜索 | 「帮我找护肤品」「200 块以内的数码产品」 |
| 比价 | 「雅诗兰黛小棕瓶哪个平台最便宜」 |
| 推荐 | 「油性皮肤有什么护肤品推荐」「送女朋友什么礼物好」 |
| 下单 | 「我要买这个」（触发 HITL 人工确认） |
| 边界 | 「面霜 300 以内」（无解预算 → 放宽并给最接近替代）、「越便宜越好」（歧义 → 消歧） |

## API

- `POST /api/chat` — 发送消息，返回 SSE 流（事件：`intent` / `entities` / `clarification` / `results` / `explanation` / `interrupt` / `done` / `error`）
- `POST /api/chat/resume` — HITL 中断后恢复（确认/取消下单）
- `POST /api/auth/*` — 注册/登录/刷新（Java 控制面）
- `GET /.well-known/jwks.json` — JWT 公钥（Java）

## 测试与评估

```bash
python -m pytest tests/                       # Python 回归/集成测试
python scripts/eval_recommendation.py         # 推荐质量 Eval（需 Qdrant + Ollama）
cd shopmesh-java && mvn test                  # Java 测试（H2 内存库）
```

配置集中在 `config.yaml`，密钥用 `${ENV_VAR}` 引用（`src/config.py` 加载时解析）。

## License

MIT
