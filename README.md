# ShoppingAgent

基于 LangGraph 的多 Agent 智能导购系统。用户通过自然语言描述购物需求，Agent 自动完成意图识别、商品检索、多目标排序、促销计算，并给出个性化推荐理由。

## 核心功能

- **意图分类** — 自动识别用户意图：搜索、比价、推荐、商品详情、下单
- **实体提取** — 从自然语言中提取品类、价格范围、品牌、场景等结构化信息
- **智能追问** — 信息不足时主动追问，最多 3 轮，避免返回无关结果
- **混合检索** — 语义向量（BGE-M3）+ 结构化过滤，跨平台搜索 30+ 商品
- **促销计算** — 自动计算满减、阶梯折扣、运费等促销到手价
- **多目标排序** — 综合相关性、价格、口碑、时效、个性化 5 个维度排序
- **个性化推荐** — 为 Top 3 商品生成自然语言推荐理由
- **Human-in-the-Loop** — 下单前需用户确认，支持中断与恢复
- **SSE 流式输出** — 前端实时展示推理过程（意图 → 实体 → 结果 → 推荐）
- **购物闭环** — 购物车 → 下单 → 支付（沙箱）→ 退款/订单历史，见下方"工程亮点"

## 工程亮点（迭代记录）

> 这一节是这个项目真正有分量的部分：不是功能清单，而是**工程判断与量化改进**。

**架构收敛**：早期仓库里并存 5 套执行编排（workflow / multi_agent / orchestrator / dag_executor / dag_engine），删除死代码并**收敛为单一主路径**（确定性意图路由 → 每意图一个 ReAct agent）；评估后主动砍掉"LLM 动态拆解任务 DAG"——导购意图空间封闭，单 ReAct agent 足够，DAG 只增不确定性。

**延迟优化 116s → 22–39s**（单一慢推理模型、无法换模型的前提下，靠减少在线 LLM 调用次数）：
- 逐商品推荐文案改用排序器已算好的确定性理由（去掉 3 次 LLM）
- 收尾总结模板化（再去 1 次 LLM）
- 简单首轮 query 用规则/关键词抽实体，跳过 ~15s 的抽取 LLM（preprocess 20s→2.6s）
- 收敛 agent 冗余工具调用 + 去重守卫

**生产级 agent 交易设计**：核心判断是**不让 LLM 自主花钱**——LLM 只提议和编排，实际下单/支付走确定性代码 + 人工确认闸门 + 服务端二次校验；库存 Redis 原子预占防超卖、下单幂等键防重复扣款、支付只信 HMAC 验签 webhook（伪造回调被拒）、Celery 定时取消超时订单。

**基础设施排障**：定位并修复 `BaseHTTPMiddleware` 缓冲 SSE 导致流式失效、`localhost` 解析到 IPv6 空 Ollama 实例、会话历史因 `_try_db` 误判静默丢失、Celery 任务从未注册等一批隐藏问题。

**Eval 驱动**：`scripts/eval_recommendation.py` 用可复现指标量化推荐质量（预算/品类/product_type/场景命中率），首跑即定位空结果召回缺陷 —— **用数据驱动改进，而非"感觉还行"**。

## 技术栈

| 层 | 技术 |
|---|------|
| 后端框架 | Python 3.11+ / FastAPI |
| Agent 编排 | LangGraph (StateGraph) |
| LLM | OpenAI 兼容 API（可切换） |
| Embedding | BGE-M3 via Ollama |
| 向量数据库 | Qdrant |
| 关系数据库 | PostgreSQL |
| 缓存 | Redis |
| 前端 | Next.js 14 / React 18 / Tailwind CSS |
| ORM | SQLModel |
| 日志 | structlog (JSON) |

## 项目结构

```
ShoppingAgent/
├── src/
│   ├── api/             # FastAPI 路由（SSE 流式接口）
│   ├── agents/          # Agent 节点（意图分类、实体提取、排序、解释）
│   ├── graph/           # LangGraph 状态图与编排
│   ├── models/          # LLM 客户端与 Embedding 模型
│   ├── retrieval/       # 混合检索（向量 + 结构化过滤）
│   ├── skills/          # Skill 注册与实现（搜索/比价/详情/下单）
│   ├── memory/          # 会话记忆与压缩
│   ├── security/        # 输入输出安全校验
│   ├── resilience/      # 错误分类与重试
│   ├── observability/   # 结构化日志与链路追踪
│   ├── db/              # 数据库与 Redis 连接
│   ├── router/          # 语义路由与意图分类
│   ├── tools/           # 工具函数（搜索、促销计算）
│   └── config.py        # 配置加载
├── frontend/            # Next.js 前端
├── data/
│   ├── mock_data.json   # 30 个商品 + 6 条促销规则
│   └── mock_data_annotations.md
├── config.yaml          # 主配置文件
├── .env.example         # 环境变量模板
├── main.py              # CLI 入口
└── requirements.txt
```

## 快速开始

### 前置依赖

- Python 3.11+
- Node.js 18+
- Docker（用于 Qdrant、PostgreSQL、Redis）
- Ollama（本地 Embedding 服务）

### 1. 启动基础设施

```bash
# Qdrant（向量数据库）
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

# PostgreSQL
docker run -d --name postgres -p 5432:5432 -e POSTGRES_PASSWORD=password -e POSTGRES_DB=shopping_agent postgres:16

# Redis
docker run -d --name redis -p 6379:6379 redis:7

# Ollama + BGE-M3
ollama pull bge-m3
ollama serve  # 默认端口 11434，config.yaml 中配置为 11505
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY
```

### 3. 启动后端

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# 或 .venv\Scripts\activate     # Windows CMD

# 安装依赖
pip install -r requirements.txt

# 导入 Mock 数据到 Qdrant
python -m data.import_data

# 启动 API 服务
uvicorn src.api.chat:app --reload --port 8000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:3000
```

### 5. 或者直接用 CLI

```bash
# 单次对话
python main.py "帮我找护肤品"

# 流式输出
python main.py --stream "帮我找护肤品"

# 交互模式
python main.py
```

## 可以尝试的提问

### 搜索类
- "帮我找护肤品"
- "有没有好喝的奶茶推荐"
- "200 块以内的数码产品"

### 比价类
- "雅诗兰黛小棕瓶哪个平台最便宜"
- "帮我比一下 iPhone 15 各平台的价格"

### 推荐类
- "我是油性皮肤，有什么护肤品推荐"
- "送女朋友什么礼物好"
- "学生党预算 100 以内买什么零食"

### 下单类
- "我要买这个"（触发 Human-in-the-Loop 确认流程）

### 边界测试
- "你好"（信息不足，触发追问）
- "越便宜越好"（歧义字段，触发消歧）

## API 接口

### `POST /api/chat`

发送消息，返回 SSE 流。

**请求：**
```json
{"message": "帮我找护肤品", "session_id": "optional-session-id"}
```

**SSE 事件：**
| 事件 | 说明 |
|------|------|
| `intent` | 意图分类结果 |
| `entities` | 提取的实体信息 |
| `clarification` | 追问问题 |
| `results` | 排序后的商品列表 |
| `explanation` | 推荐理由 |
| `interrupt` | 下单确认（HITL） |
| `done` | 流结束 + 耗时 |
| `error` | 错误信息 |

### `POST /api/chat/resume`

HITL 中断后恢复（确认/取消下单）。

### `GET /api/health`

健康检查。

## 配置说明

所有配置集中在 `config.yaml`，支持的配置项：

- `llm` — LLM 模型与 API 地址
- `embedding` — Embedding 模型（Ollama）
- `vector_db` — Qdrant 连接
- `redis` — Redis 连接
- `database` — PostgreSQL 连接
- `router` — 意图路由与语义阈值
- `memory` — 上下文窗口与压缩策略
- `logging` — 日志级别与格式

## License

MIT
