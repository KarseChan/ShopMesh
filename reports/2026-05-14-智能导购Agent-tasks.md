# 智能导购 Agent — 任务报告

> 日期：2026-05-14
> 基于：`reports/2026-05-14-智能导购Agent-idea.md`

## 总体目标

12 周内完成 MVP，跑通"一句话输入 → 推荐结果 → 模拟下单"完整链路，具备 10 个可量化的简历技术亮点。

## Agent 清单（15 个节点）

### LLM Agent（7 个，调用 LLM 推理）
| # | Agent | 位置 | 输入 | 输出 | 调用 LLM |
|---|-------|------|------|------|----------|
| 1 | Semantic Router | 感知层入口 | 用户输入 | intent + confidence | 否（BGE-M3 向量） |
| 2 | LLM Router | 感知层 | 用户输入 + top_k_intents | intent + confidence | 是 |
| 3 | Entity Extractor | 感知层 | 用户输入 | 结构化实体 + 歧义标记 | 是 |
| 4 | Disambiguator | 感知层 | 歧义实体 + 上下文 | 消歧结果 | 是 |
| 5 | Clarification Engine | 感知层 | 实体 + 信息缺失度 | 是否追问 + 追问问题 | 是 |
| 6 | Ranker | 推理层 | 候选商品 + 条件画像 | 排序结果 + 排序依据 | 是 |
| 7 | Explainer | 推理层 | 排序结果 + 用户偏好 | 推荐理由 + 对比建议 | 是 |

### 工具节点（4 个，确定性逻辑）
| # | 节点 | 位置 | 输入 | 输出 | 调用 LLM |
|---|------|------|------|------|----------|
| 8 | Hybrid Retriever | 推理层 | 结构化过滤条件 | 候选商品列表 | 否（Qdrant） |
| 9 | Promotion Calculator | 推理层 | 商品 + 促销规则 | 原价/折扣/到手价 | 否 |
| 10 | Order Skill | 执行层 | 用户确认 + 规格 | order_id + status | 否（HITL） |
| 11 | Safety Guard | 全链路 | 输入/输出/调用参数 | safe/unsafe + 原因 | 否（规则引擎） |

### 记忆与状态节点（4 个）
| # | 节点 | 位置 | 输入 | 输出 | 调用 LLM |
|---|------|------|------|------|----------|
| 12 | Memory Retriever | 感知层（按需） | 用户输入（含指代词） | 相关历史对话 Chunk | 否（Qdrant） |
| 13 | Context Assembler | 每次 LLM 调用前 | L1~L4 记忆 | 组装好的 Prompt | 否 |
| 14 | Memory Manager | 每轮对话结束 | 本轮对话 + 行为信号 | 更新 L2/L3 记忆 + RemoveMessage 修剪 State | 是（偏好提取） |
| 15 | Dialogue FSM | 全链路 | 当前状态 + 用户输入 | 状态转移 + 控制流 | 否 |

### 执行流（单轮对话）
```
用户输入
  → 1.Semantic Router → (命中) → 3.Entity Extractor ─┐
                        (未命中) → 2.LLM Router ──────┘
                                                    ↓
                            8.Memory Retriever（并行）+ 3.Entity Extractor
                                                    ↓
                            5.Clarification Engine → (需要追问 → 循环)
                                                    ↓ (不需要)
                            13.Context Assembler
                                                    ↓
                            8.Hybrid Retriever → 9.Promotion Calculator
                                                    ↓
                            6.Ranker → 7.Explainer
                                                    ↓
                            10.Order (HITL 挂起) → 14.Memory Manager → END
```

## 技术选型全景

### 检索架构：混合检索（非纯 RAG）

> **核心判断**：电商商品检索 ≠ RAG。RAG 适合知识问答（文档切片→向量化→语义检索→注入Prompt），但电商场景需要**实时结构化过滤 + 语义召回**的混合能力。

```
用户输入："帮我找一杯 20 元以内、30 分钟能送到的奶茶"
    │
    ▼
┌─────────────────────────────────────┐
│  Layer 1: Query Understanding       │  LLM Function Calling
│  输出: {category:"奶茶", price≤20,  │  自然语言 → 结构化查询
│         delivery≤30min,             │
│         semantic:"奶盖 芋泥 口味"}  │
└──────────┬──────────────────────────┘
           │
     ┌─────┴─────┐
     ▼           ▼
┌─────────┐ ┌──────────────┐
│结构化过滤│ │ 语义检索      │  ← 并行执行
│(精确匹配)│ │(向量相似度)   │
│          │ │              │
│price≤20  │ │"奶茶 奶盖    │
│category  │ │ 芋泥口味"    │
│=奶茶     │ │              │
│delivery  │ │ → Top 50     │
│≤30min    │ │   候选       │
└────┬─────┘ └──────┬───────┘
     │              │
     └──────┬───────┘
            ▼
┌─────────────────────────────────────┐
│  结果融合（交集优先，补集兜底）+ 去重│
└──────────┬──────────────────────────┘
           ▼
┌─────────────────────────────────────┐
│  Layer 3: Multi-Objective Re-ranking│  LLM 精排
│  综合：相关性/价格/口碑/时效/个性化  │
│  输出：排序结果 + 每个商品的推荐理由 │
└─────────────────────────────────────┘
```

| 层次 | 技术 | 说明 |
|------|------|------|
| Query Understanding | LLM + Function Calling | Prompt 输出结构化 JSON |
| 结构化过滤 + 语义检索 | **Qdrant 原生预过滤** | HNSW 遍历中跳过不满足条件的向量，一步完成，召回率 ~100% |
| 向量 Embedding | **BGE-M3**（支持 CPU 推理） | 中文语义向量模型，768 维 |
| 向量化文本 | 品类+品牌+商品名+特征+场景 | 增强版构造，非直接用商品名 |
| 重排序 | LLM-based scoring | 直接让 LLM 打分排序 + 推荐理由 |

### 业内方案对标

| 公司/产品 | 检索方案 | 核心差异 |
|-----------|----------|----------|
| **千问导购** | Function Calling → 淘宝实时 API | 依赖淘宝 40 亿商品库和实时数据 |
| **京东言犀** | NL2SQL + 知识图谱 + 搜索 API | 结构化商品库 + 知识图谱推理 |
| **Amazon Rufus** | Product Graph + LLM Re-ranking | 产品关系图谱 + 个性化重排 |
| **Perplexity Shopping** | Web 检索 + 结构化提取 + 推荐 | 实时爬取 + 结构化 |
| **ChatGPT Shopping** | Bing Shopping API + GPT 推理 | 搜索引擎驱动 |

**本项目定位**：对标千问架构流，用开源方案（Qdrant + BGE-M3 + LangGraph）替代淘宝内部 API，面试中展示"我知道大厂怎么做，我也能用开源方案做到"。

### 前端框架：两阶段策略

| 阶段 | 框架 | 目的 |
|------|------|------|
| **Phase 0-1**（快速验证） | Streamlit | 1 天搭好，专注 Agent 逻辑验证 |
| **Phase 3-5**（简历项目） | **React + Next.js 14 + Tailwind + shadcn/ui** | 视觉效果好，展示全栈能力 |

**前端技术栈明细**：

| 层 | 技术 | 理由 |
|----|------|------|
| 框架 | Next.js 14 (App Router) | SSR + API Routes 一体化 |
| UI | Tailwind CSS + shadcn/ui | 组件质量高，开箱即用 |
| 状态管理 | Zustand | 轻量，适合对话状态 |
| 实时通信 | SSE (Server-Sent Events) | LLM 流式输出 |
| 商品卡片 | 自定义组件 | 图片 + 价格 + 推荐理由 + 左右滑动对比 |

### 任务总览

```
Phase 0  骨架搭建          Week 1       ← 所有后续任务的前置
Phase 1  感知层            Week 2-3
Phase 2  推理层            Week 4-6     ← 核心技术亮点集中区
Phase 3  执行层 + 前端     Week 7-8
Phase 4  深度优化 + 评测   Week 9-10
Phase 5  简历包装          Week 11-12
```

---

## Phase 0：骨架搭建（Week 1）

> 目标：跑通单轮"搜索 → 推荐"最小链路

### T0.1 项目脚手架初始化
- **描述**：创建项目目录结构、依赖管理、环境配置
- **复杂度**：低
- **依赖**：无
- **Python 版本**：3.11+（LangGraph 要求 3.10+，推荐 3.11 获得最佳性能）
- **本地环境要求**：
  - Ollama + BGE-M3（`bge-m3:latest`，本地 Embedding 推理，已安装）
  - Docker Desktop（运行 Qdrant + Redis + PostgreSQL）
  - 国内模型 API Key（Qwen / DeepSeek / 智谱，任选一个）
  - Node.js 18+（Phase 3 前端开发时才需要）
- **requirements.txt**：
  ```
  # === Web 框架 ===
  fastapi>=0.115.0
  uvicorn[standard]>=0.34.0

  # === DAG 编排 ===
  langgraph>=0.4.0

  # === LLM / Embedding ===
  httpx>=0.27.0                   # 异步 HTTP（LLM API + Ollama Embedding）

  # === 向量数据库 ===
  qdrant-client>=1.12.0

  # === 结构化数据 ===
  sqlmodel>=0.0.22                # ORM（Pydantic + SQLAlchemy）
  alembic>=1.14.0                 # 数据库迁移
  psycopg2-binary>=2.9.9         # PostgreSQL 驱动

  # === 会话存储 ===
  redis>=5.2.0

  # === 配置管理 ===
  pydantic>=2.9.0
  pydantic-settings>=2.6.0
  pyyaml>=6.0.2

  # === 工具 ===
  python-dotenv>=1.0.1           # .env 文件加载
  structlog>=24.4.0              # 结构化日志（JSON 格式）
  ```
- **Docker 启动命令**（三个服务，一次启动）：
  ```bash
  # Qdrant 向量数据库
  docker run -d --name shopping-qdrant -p 6333:6333 -p 6334:6334 -v qdrant_data:/qdrant/storage qdrant/qdrant

  # Redis 会话存储
  docker run -d --name shopping-redis -p 6379:6379 -v redis_data:/data redis:7

  # PostgreSQL 结构化数据库
  docker run -d --name shopping-postgres -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=shopping_agent -v pg_data:/var/lib/postgresql/data postgres:16
  ```
  - 所有数据挂载 volume，容器重建不丢数据
  - 启动后可通过 `docker ps` 确认三个容器 running
- **Ollama 本地模型**：
  ```bash
  # Embedding（已安装）
  ollama pull bge-m3:latest
  ```
- **模型切换成本**：
  - LLM 切换：改 `config.yaml` 中 `base_url` + `model`，Agent 代码零改动
  - 推荐：Qwen-Plus（阿里云百炼）/ DeepSeek-V3（深度求索），约 ¥1-2 / 百次请求
- **产出**：
  - `requirements.txt`：完整依赖清单（含版本锁定）
  - `pyproject.toml`：项目元数据 + 构建配置
  - 基础目录：`src/agents/`, `src/tools/`, `src/graph/`, `src/models/`, `src/db/`, `tests/`, `data/`
  - `.env.example`：环境变量模板（DATABASE_URL / REDIS_URL / QDRANT_URL / QWEN_API_KEY）
  - `config.yaml`：集中配置文件（默认使用本地 Ollama + BGE-M3）

### T0.2 LangGraph 环境搭建 + Hello World
- **描述**：安装 LangGraph，跑通官方最简示例（单节点图），理解 State/Node/Edge 核心概念
- **复杂度**：低
- **依赖**：T0.1
- **产出**：
  - 一个可运行的最小 LangGraph 图
  - 理解 `StateGraph`, `add_node`, `add_edge`, `compile` 的用法

### T0.3a Mock 精选数据（30 个商品 + 4 类测试用例，Demo 用）
- **描述**：生成 3 个关联 JSON 数组（platforms/promotions/products），30 个精选商品覆盖 8 个品类，刻意注入 4 类边界测试用例，用于 Demo 演示和功能验证
- **复杂度**：中
- **依赖**：T0.1
- **数据规模**：
  - 3 个平台（京东/淘宝/拼多多）
  - 6 条促销规则（满减×2 + 阶梯折扣×2 + 运费陷阱×2）
  - 30 个商品（护肤 4 / 奶茶 3 / 数码 6 / 服饰 4 / 食品 3 / 家居 4 / 母婴 2 / 运动 2）
- **刻意注入的测试用例**：
  - **跨平台比价**：3 组同款商品（雅诗兰黛/iPhone 15/维达抽纸）上架不同平台，不同价格和促销
  - **缺货陷阱**：Nike AF1（¥799, stock=0）、Babycare 纸尿裤（¥129, stock=0）、SK-II 神仙水（¥1540, stock=0）
  - **数量追问触发器**：古茗奶茶（买3件打7折）、维达抽纸（买3件打7折）
  - **运费陷阱**：索尼耳机（¥5 + ¥50运费）、杂牌精华（¥9.9 + ¥35运费）
- **产出**：
  - `data/mock_data.json`：完整的 3 关联数组（platforms + promotions + products）
  - `data/mock_data_annotations.md`：测试用例注释 + 面试 Demo 流程建议
  - 每条商品包含 `embedding_text` 字段：`{category} {brand} {name} {features按空格连接}`（供 T0.6 向量化使用）

### T0.3b 批量 Mock 数据生成（评测 + 漏斗陷阱验证用）
- **描述**：编写批量生成脚本，产出 5,000 条 Mock 商品数据，用于评测（T4.2）和 FAISS vs Qdrant 漏斗陷阱对比实验
- **复杂度**：中
- **依赖**：T0.1
- **为什么要 5,000 条**：
  - 30 个商品时，FAISS Top-50 直接召回全量库，后置过滤召回率也是 100%，无法体现"漏斗陷阱"
  - N >> K（底库 5,000，Top-K=50）时，FAISS 后置过滤才会出现高价商品占据 Top-K 导致过滤后结果稀疏的问题
  - 面试中展示"5,000 条数据，FAISS 后置过滤召回率 40-70%，Qdrant 预过滤召回率 ~100%"有说服力
- **生成策略**（纯 Python 规则生成，零 LLM 调用）：
  - **规则排列组合**：8 品类 × 25 品牌 × 5 系列 × 5 价位段 = 5,000 条（确定性生成，可复现）
  - **字段模板**：brand + series + category + price_range 组合生成商品名、特征、embedding_text
  - **价格分布**：每个品类内正态分布，确保有足够低价和高价商品形成过滤梯度
  - **促销覆盖**：随机挂载促销规则（满减/阶梯折扣/无促销），覆盖率 ~60%
  - **平台分布**：每个商品随机分配 1-3 个平台，确保跨平台比价场景
- **数据质量保证**：
  - 保留 T0.3a 的 30 个精选商品（Demo 数据与评测数据共存）
  - 边界 case 不批量生成，只保留手工注入的 4 类测试用例
  - 生成脚本可重复运行，seed 固定确保结果可复现
- **产出**：
  - `scripts/generate_mock_products.py`：批量生成脚本（参数化：品类数/品牌数/总量/seed）
  - `data/mock_products_5k.json`：5,000 条商品数据
  - 验证：生成后用 FAISS Top-50 + 后置过滤 vs Qdrant 预过滤跑一次，确认召回率差异真实存在

### T0.4 最小 Agent 跑通链路
- **描述**：单 Agent + 1 个搜索工具 + 硬编码排序，完成"输入需求 → 返回推荐"最小闭环
- **复杂度**：中
- **依赖**：T0.2, T0.3a
- **产出**：
  - 一个 LangGraph 图：`input → LLM解析 → 搜索工具 → 排序 → output`
  - 能处理"帮我找一杯奶茶"并返回 3 个推荐结果
  - 基础 `main.py` 可命令行运行

### T0.5 模型配置解耦 + 数据层设计
- **描述**：设计三层模型工厂（LLM / Embedding / VectorStore）+ SQLModel 数据层，所有模型配置集中在 config.yaml，Agent 代码零改动即可切换模型
- **复杂度**：中
- **依赖**：T0.1
- **解耦设计**：
  - **LLM 工厂**：`LLMFactory.get_llm(agent_name)` → 根据 config 中该 Agent 的配置返回对应 LLM 实例，支持不同 Agent 用不同模型
  - **Embedding 工厂**：`EmbedderFactory.get_embedder(agent_name)` → 根据配置返回对应 Embedding 模型
  - **VectorStore 工厂**：`VectorStoreFactory.get_store()` → 根据配置返回 Qdrant / FAISS 实现，抽象接口统一
  - **配置中心**：`config.yaml` 集中管理所有模型配置
  - **切换成本**：换 LLM 改 config 中 base_url + model + api_key，Embedding 始终用本地 Ollama bge-m3，Agent 代码零改动
- **config.yaml 默认配置**：
  ```yaml
  llm:
    default:
      provider: openai_compatible
      base_url: https://dashscope.aliyuncs.com/compatible-mode/v1   # 阿里云百炼
      model: qwen-plus
      api_key: ${QWEN_API_KEY}
      temperature: 0.1
      max_tokens: 2048
    # DeepSeek 示例
    # base_url: https://api.deepseek.com/v1
    # model: deepseek-chat
    # api_key: ${DEEPSEEK_API_KEY}

  embedding:
    default:
      provider: ollama
      model: bge-m3:latest
      base_url: http://localhost:11434

  vector_db:
    provider: qdrant
    url: http://localhost:6333
    collection: products

  redis:
    url: redis://localhost:6379

  database:
    url: postgresql://postgres:postgres@localhost:5432/shopping_agent
  ```
- **SQLModel 数据层设计**：
  - **选型理由**：FastAPI 作者开发，Pydantic BaseModel + SQLAlchemy ORM 融合，一套定义同时做数据验证和数据库映射
  - **共用优势**：Entity Extractor 输出的 Pydantic Schema、Tool 的参数定义、商品模型 → 直接变成数据库表定义，零样板代码转换
  - **核心模型**：
    - `Product`（商品）：直接从 mock_data.json 的 schema 映射
    - `UserProfile`（L3 条件画像）：品类隔离的偏好数据
    - `Session`（会话）：session_id + user_id + created_at
    - `Order`（订单）：幂等性保证，HITL 生成
    - `IntentSample`（意图样本）：Semantic Router 的样本库持久化
  - **Engine 管理**：`src/db/engine.py` 统一管理 PostgreSQL 连接
  - **迁移**：Alembic 管理 schema 变更
- **产出**：
  - `config.yaml`：统一配置文件（默认 Ollama + BGE-M3 + Qdrant + Redis + PostgreSQL）
  - `src/models/llm_client.py`：LLM 工厂（OpenAI-compatible，支持 Qwen / DeepSeek 等，实例缓存）
  - `src/models/embedder.py`：Embedding 工厂（Ollama 客户端调用本地 bge-m3，async 接口）
  - `src/retrieval/vector_store.py`：VectorStore 抽象接口 + Qdrant 实现
  - `src/db/models.py`：SQLModel 数据模型（Product / UserProfile / Session / Order / IntentSample）
  - `src/db/engine.py`：数据库引擎管理（PostgreSQL）
  - 验证：改 config.yaml 中的 default 模型，重启后系统正常运行

### T0.6 向量数据库搭建 + 商品向量化（Qdrant）
- **描述**：搭建 Qdrant 向量数据库，使用 BGE-M3 对 Mock 商品数据做向量化，利用 Qdrant 原生 Payload 预过滤避免 FAISS 后置过滤的漏斗陷阱
- **复杂度**：中
- **依赖**：T0.3a
- **向量化策略**：
  - 向量化文本 = `品类 + 品牌 + 商品名 + 核心特征 + 适用场景`（增强版）
  - 示例："护肤精华 雅诗兰黛小棕瓶 修护抗老保湿 适合25-35岁 日常护肤送礼"
  - Payload 存储（Qdrant 原生支持过滤）：category / brand / price / rating / stock / delivery_minutes / platform / promotion
- **向量库选型**：Qdrant（MVP 即用）
  - 理由：原生 Payload 预过滤，HNSW 遍历时直接跳过不满足条件的向量，召回率 ~100%
  - 对比 FAISS：FAISS Top-K → Python 过滤 → 漏斗陷阱（召回率 40-70%）
  - 部署：`docker run -d --name qdrant -p 6333:6333 -p 6334:6334 -v ./docker/qdrant_storage:/qdrant/storage qdrant/qdrant`（挂载 volume 持久化，容器重建不丢数据）
- **BGE-M3 部署方案（Ollama 本地，天然异步）**：
  - 通过 Ollama HTTP API 调用本地 BGE-M3，天然异步，**无阻塞事件循环问题**
  - Ollama 在后台常驻，模型启动后常驻内存，无冷启动开销
    ```python
    # src/retrieval/embedder.py
    import httpx

    class Embedder:
        def __init__(self, base_url: str = "http://localhost:11434"):
            self._base_url = base_url
            self._model = "bge-m3:latest"

        async def aembed(self, text: str) -> list[float]:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{self._base_url}/api/embed", json={
                    "model": self._model,
                    "input": text
                })
                return resp.json()["embeddings"][0]

        async def aembed_batch(self, texts: list[str]) -> list[list[float]]:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{self._base_url}/api/embed", json={
                    "model": self._model,
                    "input": texts
                })
                return resp.json()["embeddings"]
    ```
- **产出**：
  - `src/retrieval/vector_store.py`：Qdrant 封装（create_collection / upsert / search_with_filter）
  - `src/retrieval/embedder.py`：Embedding 模型封装（Ollama HTTP API 调用本地 bge-m3:latest，天然异步）
  - `scripts/build_index.py`：一键重建索引脚本（读取 Mock 数据 → 构造向量化文本 → 生成 Embedding → 写入 Qdrant）
  - 验证：输入"20 元以内奶茶"→ Qdrant 原生预过滤 + 语义检索 → 返回 10 个同时满足价格和语义的商品

### T0.7 结构化日志基础设施
- **描述**：搭建 JSON 结构化日志体系，定义统一日志格式、双 ID 关联机制、全链路埋点规范，为所有后续模块提供日志能力
- **复杂度**：中
- **依赖**：T0.1
- **日志格式**（所有模块统一）：
  ```json
  {
    "timestamp": "ISO8601",
    "level": "INFO|WARN|ERROR|DEBUG",
    "request_id": "req_xxx",
    "session_id": "sess_xxx",
    "module": "模块名",
    "step": "步骤名",
    "event": "事件类型",
    "model": "模型名（LLM 调用时）",
    "tool": "工具名（Tool 调用时）",
    "input_length": 42,
    "output_length": 128,
    "duration_ms": 45,
    "extra": {}
  }
  ```
- **正常请求埋点**（6 个关键节点）：
  - `semantic_router` → `intent_resolved`（意图识别，含 confidence / latency）
  - `entity_extractor` → `entities_extracted`（实体提取，含 model / tokens）
  - `context_assembler` → `context_built`（上下文组装，含 memory_layers / token_budget / used）
  - `hybrid_retriever` → `search_complete`（检索完成，含 results_count / filters / latency）
  - `ranker` → `ranking_done`（排序完成，含 candidates / model / tokens）
  - `explainer` → `response_generated`（生成完成，含 total_latency）
- **错误日志增强**（比正常日志多 5 个字段）：
  - `error_type`：异常类名（如 `RateLimitError`、`ConnectionTimeout`）
  - `error_code`：HTTP 状态码或自定义错误码
  - `error_message`：人类可读错误描述
  - `attempt`：当前第几次尝试（配合 T3.5 重试策略）
  - `fallback_action`：降级动作（`retry_with_backoff` / `degrade_to_db_query` / `skip_memory`）
  - `degraded`：是否已降级运行
- **双 ID 关联**：
  - `request_id`：每个请求唯一生成（`req_{uuid8}`），串联单次请求从进入到返回的所有日志
  - `session_id`：从请求 Header 或 Cookie 获取，串联同一用户跨多轮对话的所有日志
  - 支持按 `request_id` 一键拉取完整调用链，按 `session_id` 回溯用户完整会话
- **日志输出**：
  - 控制台：开发环境彩色格式化输出
  - 文件：生产环境 JSON Lines 格式（`.jsonl`），按天轮转
  - 可选：接入 ELK / Grafana Loki（面试加分项，非 MVP 必须，谨慎实现）
- **产出**：
  - `src/observability/logger.py`：统一日志工厂（JSON 格式化 + 双 ID 注入 + 请求上下文传播）
  - `src/observability/trace.py`：请求追踪装饰器（自动记录 module / step / duration_ms）
  - `src/observability/error_logger.py`：错误日志增强器（自动附加 error_type / attempt / fallback_action）
  - `src/observability/middleware.py`：FastAPI 中间件（自动生成 request_id / session_id，注入请求上下文）
  - `config.yaml` 中增加 `logging` 配置段（level / format / file_path / rotation）
  - 验证：模拟一次完整请求 + 一次错误请求，检查日志输出格式和字段完整性

---

## Phase 1：感知层（Week 2-3）

> 目标：实现智能需求澄清 + 意图识别

### T1.1 意图分类器（Semantic Router + LLM Fallback 快慢系统）
- **描述**：两层意图分类——Semantic Router 快系统（BGE-M3 向量相似度，~5ms）+ LLM 慢系统 Fallback（~800ms），70% 请求可被快系统直接命中
- **复杂度**：中
- **依赖**：T0.5, T0.6
- **快慢系统设计**：
  - **快系统（Semantic Router）**：用户 Query → BGE-M3 向量化 → 与意图样本库（每意图 10-20 条高频 Query）计算余弦相似度 → 置信度 > 阈值 `τ` 直接路由
  - **慢系统（LLM Fallback）**：置信度 < `τ` 时调用 LLM Function Calling 精确分类
  - **意图覆盖**：搜索 / 比价 / 推荐 / 详情 / 下单（5 种）
  - **动态学习**：LLM Fallback 的高置信度结果自动加入样本库，命中率随使用逐步提升
- **阈值标定（Calibration）——不拍脑袋定 τ**：
  - **坑点**：BGE-M3 768 维稠密向量存在各向异性（Anisotropy），不相关句子余弦相似度可能 > 0.75，相近表述可能仅 0.82-0.88。硬编码 0.9 会导致快系统几乎永远不命中
  - **标定流程**：
    1. 构造正样本对：每个意图 15 条样本，两两配对（同意图内，预期相似度高）
    2. 构造负样本对：不同意图间随机配对（预期相似度低）+ 跨意图相似表述（如"推荐一部手机" vs "推荐一家餐厅"，难度最高的负样本）
    3. 计算所有样本对的余弦相似度，绘制分布直方图
    4. 选择 τ 使 F1-score 最大化（在正负样本分布的交集区域找最优切分点）
    5. 记录 τ 值到 `config.yaml`，不硬编码在代码中
  - **预期结果**：BGE-M3 的最优 τ 可能在 0.80-0.88 之间，而非直觉的 0.9
  - **配置化**：`config.yaml` 中 `semantic_router.threshold`，可在不改代码的情况下调优
  - **降级保障**：如果标定结果导致快系统命中率 < 50%，说明样本库质量不足，应降级为纯 LLM 分类直到样本库扩充到位
- **收益**：
  - TTFB：800ms → 245ms（加权平均，70% 命中快系统）
  - Token 节省：70%（快系统不消耗 Token）
- **产出**：
  - `src/router/semantic_router.py`：Semantic Router（Qdrant 意图样本库 + 余弦相似度匹配，阈值从 config 读取，复用 T0.6 的 async Embedder）
  - `src/router/llm_router.py`：LLM Fallback 分类器
  - `src/router/intent_classifier.py`：统一入口（Semantic Router → LLM Fallback）
  - `src/router/adaptive_learner.py`：动态学习（LLM 结果自动扩充样本库）
  - `scripts/calibrate_threshold.py`：阈值标定脚本（正负样本对 → 余弦分布 → F1 最优 τ → 写入 config.yaml）
  - `data/intent_samples.json`：5 种意图 × 15 条高频 Query 样本
  - 测试：验证快系统命中率、Fallback 触发率、动态学习效果、阈值标定结果

### T1.2 结构化实体提取
- **描述**：从用户输入中抽取品类/价格/品牌/场景/数量等结构化字段
- **复杂度**：中
- **依赖**：T0.5
- **产出**：
  - `src/agents/entity_extractor.py`
  - 输出 Schema：`{category, price_range, brand, scenario, quantity, ...}`
  - 歧义标记：当实体不确定时标记 `ambiguous: true`

### T1.3 需求澄清引擎
- **描述**：基于信息缺失度的追问策略，非固定模板
- **复杂度**：高
- **依赖**：T1.1, T1.2
- **产出**：
  - `src/agents/clarification_engine.py`
  - 追问优先级计算：缺失度 × 区分度
  - 停止条件：候选集 < 20 或追问 >= 3 轮
  - 追问模板库：15+ 场景覆盖
  - 测试：模拟多轮对话，验证追问策略

### T1.4 实体消歧模块
- **描述**：处理歧义实体（"苹果"= 水果 or 手机？）
- **复杂度**：中
- **依赖**：T1.2
- **产出**：
  - `src/agents/disambiguator.py`
  - 基于上下文的消歧策略
  - 消歧失败时主动询问用户

### T1.5 四级记忆系统 + 上下文压缩
- **描述**：设计 L1 工作记忆 / L2 对话记忆 / L3 用户画像 / L4 知识库四级记忆架构，实现跨会话持久化和上下文窗口管理
- **复杂度**：高
- **依赖**：T0.5
- **记忆层级**：
  - **L1 工作记忆**（内存）：当前任务中间状态，单次调用生命周期
  - **L2 对话记忆**（Redis + Qdrant）：三层互补——L2a 滑动窗口（最近 5 轮完整）+ L2b LLM 摘要（宏观偏好）+ L2c 向量检索（历史实体精确召回）
  - **L3 条件画像**（DB）：品类隔离 + 场景隔离，解决全局画像均值化、Ranker 权重错位、隐式反馈无上下文等问题
    - 全局基础画像：仅保留跨品类通用特征（注册时间、总订单数、配送偏好、偏好平台）
    - 品类条件画像：每个品类独立维护 price_sensitivity / price_range / preferred_brands / style / visit_count
    - 场景条件画像：送礼（预算上浮 50%、品牌优先级高）vs 日常自用（预算下浮 20%、便利性优先）
    - Context Assembler 按当前品类条件注入画像（非全局注入），避免注入干扰引发幻觉
    - Ranker 使用品类条件化权重排序，避免"高消费"标签导致低价商品被降权
    - 隐式反馈上下文化：行为信号绑定品类 + 场景，不作为孤立动作处理
    - 新品类冷启动：相似品类迁移 > 全局画像兜底 > 快速探测（前 2-3 次不注入画像）
  - **L4 知识库**（Qdrant + DB）：商品/品类/促销知识，永久存储
- **L2c 向量记忆（Memory as Retrieval）**：
  - 写入：每轮对话异步 Chunking → BGE-M3 向量化 → 存入用户专属 Qdrant Collection
  - Chunk 结构：`user_input + assistant_output + extracted_entities + intent + category + timestamp`
  - 召回触发：包含指代词（"上次"/"那个"/"之前看的"）或跨品类跳跃时触发
  - 召回流程：Query 构建 → Qdrant Top-5（原生 Payload 过滤）→ 原封不动注入 Prompt
  - 优势：解决摘要丢失实体（"古茗 15 元"）和长程跳跃唤醒（"买第一天那台电脑"）两个痛点
- **上下文压缩策略**：
  - 滑动窗口：保留最近 5 轮完整 + 历史摘要 + 偏好锚点
  - 触发条件：总 token > 上下文窗口 60% 时压缩
  - 压缩方式：LLM 将历史消息摘要为 200 字以内
  - L2c 向量检索不受窗口限制：被滑出窗口的历史对话仍可通过语义检索召回
- **上下文组装（Context Assembler — 优先级背包算法）**：
  - 总预算 = model_context_window × 80%（预留 20% 输出空间）
  - **7 级优先级队列**（P0 最高，按优先级装入 Token 预算）：
    - P0 System Prompt（固定，~300 tokens）
    - P1 当前用户输入（固定，~200 tokens）
    - P2 L1 工作记忆——当前任务中间状态（固定，~300 tokens）
    - P3 检索命中的商品/促销信息（弹性，最小 200 / 典型 800 tokens，裁剪方式：保留 Top-3→Top-1）
    - P4 L2c 向量召回的历史对话（弹性，最小 0 / 典型 400 tokens，裁剪方式：保留最近 1 条→0 条）
    - P5 最近 3 轮滑动窗口 L2a（弹性，最小 100 / 典型 600 tokens，裁剪方式：3 轮→1 轮）
    - P6 用户画像摘要 L3 条件画像（弹性，最小 50 / 典型 200 tokens，裁剪方式：完整画像→仅品类偏好）
    - P7 L2b 历史摘要 + 宽泛知识（弹性，最小 0 / 典型 300 tokens，可直接丢弃）
  - **装匣逻辑**：剩余预算 >= 典型大小 → 满额注入；>= 最小大小 → 裁剪注入；< 最小大小 → 丢弃（仅 P4/P7）
  - **冷启动优势**：全新用户无对话历史，P4/P5/P7 预算全部省给 P3 检索结果，推荐更精准
  - 知识按需加载：只检索与当前输入相关的知识片段，非全量注入
  - L2c 按需召回：触发检测 → 语义检索 → 注入"相关历史记忆"区域
- **产出**：
  - `src/memory/working_memory.py`：L1 工作记忆（LangGraph State）
  - `src/memory/session_memory.py`：L2a 滑动窗口 + L2b 摘要压缩 + RemoveMessage 修剪（Redis 封装）
  - `src/memory/memory_retriever.py`：L2c 向量记忆（写入 + 触发检测 + 语义召回）
  - `src/memory/user_profile.py`：L3 条件画像（品类隔离 + 场景隔离 + 冷启动策略，SQLModel 持久化 UserProfile 表）
  - `src/memory/knowledge_base.py`：L4 知识库（Qdrant + 结构化数据）
  - `src/memory/context_assembler.py`：上下文组装器（优先级背包算法 + 按需加载 + L2c 注入 + 裁剪策略）
  - `src/memory/compressor.py`：对话压缩（LLM 摘要 + 滑动窗口）
  - 偏好信号提取：显式反馈（用户说"太贵了"）+ 隐式行为（点击低价商品）
  - 测试：模拟 20 轮对话验证压缩效果，模拟"上次那杯奶茶"验证 L2c 召回，模拟重启验证画像持久化，验证 RemoveMessage 修剪后 State 体积（单轮 < 10KB）

### T1.6 对话状态机
- **描述**：定义明确的状态转移，管理整个对话生命周期
- **复杂度**：高
- **依赖**：T1.1, T1.3
- **产出**：
  - `src/agents/dialogue_fsm.py`
  - 状态定义：`idle → intent_detected → clarifying → searching → presenting → ordering`
  - 意图切换检测：新输入与当前状态冲突时主动确认
  - 回退机制：失败时回退到上一个稳定状态
  - 防抖：快速连续输入合并处理

---

## Phase 2：推理层（Week 4-6）

> 目标：多 Agent 协同 + DAG 编排 + 多目标排序。**简历核心亮点集中区。**

### T2.1 Router Agent 实现
- **描述**：根据 T1.1 意图分类结果（Semantic Router 或 LLM Fallback），路由到对应的 Specialist Agent
- **复杂度**：中
- **依赖**：T1.1
- **产出**：
  - `src/agents/router.py`：接收意图分类结果，分发到 Specialist Agent
  - 路由表配置化（JSON/YAML）：intent → specialist_agent 映射
  - 兜底策略：无法识别时走通用 Agent
  - 与 Semantic Router 的衔接：快系统直接路由（跳过 LLM），慢系统经 LLM 分类后路由

### T2.2 DAG 编排引擎 + Agent 通信机制 + Checkpointer
- **描述**：基于 LangGraph 构建 DAG 编排引擎，实现 Shared State 通信模型，配置 Checkpointer 为 HITL 打基础
- **复杂度**：高
- **依赖**：T0.2
- **通信模式**：Shared State（共享状态）+ Reducer 归约器
  - 定义 `ShoppingState` TypedDict，所有 Agent 读写同一 State 对象
  - 每个 Agent 只读自己需要的字段，只写自己负责的字段（职责隔离）
  - **Reducer 陷阱与解法**：
    - **坑点**：LangGraph 默认 TypedDict 是全量覆盖（Overwrite）。并行节点（如 Entity Extractor ∥ Memory Retriever）如果都向 `messages` 追加内容，后写入的会覆盖先写入的，或抛出 `InvalidUpdateError`
    - **解法**：需要并行追加的字段必须用 `Annotated[list, add]` 声明为归约字段，LangGraph 自动将多次写入合并为 list extend
  - **Checkpointer I/O 膨胀陷阱**：
    - **坑点**：LangGraph Checkpointer 在每个节点执行完毕（Superstep）后全量快照整个 State。如果 `messages` 无限累积，第 10 轮对话 State 可能膨胀到 100KB，10 个节点 = 1MB/请求的 DB 高频写入，毫秒级检索优化全死在 I/O 上
    - **解法 1（messages 修剪）**：每轮对话结束时，Memory Manager 节点使用 LangGraph 原生 `RemoveMessage` 将滑出窗口的历史消息物理删除，State 维持最小体积
    - **解法 2（日志不进 State）**：执行日志（遥测数据）不存入 State，直接调用 T0.7 的 `logger.info()` 异步落盘。State 只存影响下一步图路由决策的工作记忆
  - **State 定义**（精简，只存工作记忆）：
    ```python
    from typing import Annotated
    from langgraph.graph import add_messages, RemoveMessage

    class ShoppingState(TypedDict):
        # === 归约字段（并行节点可安全追加）===
        messages: Annotated[list, add_messages]  # 对话历史，每轮结束用 RemoveMessage 修剪
        tool_calls: Annotated[list, add]         # 工具调用记录（本轮内）

        # === 独占字段（每个 Agent 写不同字段，无需 Reducer）===
        intent: str                              # Semantic Router / LLM Router 写入
        entities: dict                           # Entity Extractor 写入
        memory_chunks: list                      # Memory Retriever 写入
        search_results: list                     # Hybrid Retriever 写入
        promotion_info: dict                     # Promotion Calculator 写入
        ranked_results: list                     # Ranker 写入
        explanation: str                         # Explainer 写入

        # === 读写字段 ===
        clarification_count: int                 # Clarification Engine 递增
        errors: Annotated[list, add]             # 错误记录，并行追加
    ```
  - **设计原则：读写分离，轻装上阵**：
    1. State = 工作记忆，只存影响图路由决策的数据
    2. 执行日志（遥测）→ `logger.info()` 异步落盘，不进 State
    3. 对话历史 → 每轮结束 `RemoveMessage` 修剪，只保留窗口内
    4. 并行追加字段 → `Annotated[list, add]`（messages / tool_calls / errors）
    5. 独占写入字段 → 普通类型（intent / entities / search_results 等）
- **并行调度策略**（延迟优化核心）：
  ```
  路径 A：快系统命中（70% 请求，目标 P95 ~2.1s）
  ┌──────────────────────────────────────────────────────────┐
  │ Semantic Router ─┬→ Entity Extractor ─┐                 │
  │   (~5ms 命中)    │                    ├→ Clarification  │
  │                  └→ Memory Retriever ─┘   Engine        │
  │                    (L2c, 并行)           (~800ms)       │
  │                                                          │
  │                              ↓ (不需要追问)              │
  │                     ┌→ Hybrid Retriever (Qdrant, ~5ms)  │
  │                     ├→ Promotion Calculator (并行)       │
  │                     └→ Context Assembler (预热, 并行)    │
  │                              ↓                           │
  │                     Ranker (~1200ms) → Explainer (~800ms)│
  └──────────────────────────────────────────────────────────┘
  串行关键路径：5 + 800 + 5 + 1200 + 800 ≈ 2.8s（含网络开销 ~2.1s）

  路径 B：慢系统 + 无澄清（25% 请求，目标 P95 ~3.5s）
  LLM Router (~800ms) 替代 Semantic Router，其余同路径 A

  路径 C：含澄清（5% 请求，目标 P95 ~5-8s）
  上述 + 1-2 轮 Clarification Engine 追问循环
  ```
  - **并行规则**：
    - Entity Extractor ∥ Memory Retriever：无数据依赖，可并行
    - Hybrid Retriever ∥ Promotion Calculator：检索结果就绪后，促销计算和上下文组装可并行
    - Ranker → Explainer：必须串行（Explainer 依赖排序结果）
    - Clarification Engine：依赖 Entity Extractor 输出，但可与 Memory Retriever 并行
  - **不能并行的瓶颈**：Ranker 和 Explainer 各需一次 LLM 调用，是关键路径上的刚性串行开销
- **Checkpointer 配置**：
  - MVP：MemorySaver（内存，开发调试用）
  - 可插拔设计：支持 Memory / SQLite / Redis 三种后端
  - 为 T3.4 的 interrupt_before HITL 机制打基础
- **产出**：
  - `src/graph/state.py`：ShoppingState 定义（TypedDict + Reducer 归约字段 + RemoveMessage 修剪）
  - `src/graph/dag_engine.py`：DAG 编排引擎（LangGraph StateGraph + Checkpointer）
  - `src/graph/checkpointer.py`：可插拔 Checkpointer 封装（Memory / SQLite / Redis）
  - `src/graph/parallel_scheduler.py`：并行调度器（Fan-out / Fan-in + 并行规则配置）
  - DAG 定义 Schema（JSON Schema）
  - 条件路由节点：if-else 分支
  - 超时熔断：单节点 2s，全局 10s（澄清路径需要更长全局超时）
  - 中间结果缓存（TTL 60s）
  - 错误降级：Agent 失败时走降级路径，不阻塞整体流程
  - 测试：构造并行 DAG + 验证 Entity Extractor ∥ Memory Retriever 并行执行 + State 体积验证（单轮 < 10KB）+ Checkpointer 持久化验证

### T2.3 混合检索引擎（Qdrant 原生预过滤 + 语义检索）
- **描述**：基于 Qdrant 原生 Payload 预过滤实现一步式混合检索，替代 FAISS 后置过滤的两路并行方案
- **复杂度**：中（Qdrant 简化了架构）
- **依赖**：T2.2, T0.3a, T0.6
- **架构决策**：
  - **弃用**：FAISS Top-K → Python 过滤（漏斗陷阱，召回率 40-70%）
  - **弃用**：两路并行 + 结果融合（架构复杂，融合逻辑难调）
  - **采用**：Qdrant 检索时直接传入 Payload 过滤条件，HNSW 遍历中跳过不满足条件的向量
  - **召回率**：~100%（所有满足条件的向量都参与排序）
  - **延迟**：~5ms（1000 条数据，与 FAISS 相当）
- **过滤策略（开发中发现的关键问题）**：
  - **简单过滤**（category/brand 精确匹配）：通过 `dict → MatchValue` 转换，传入 `store.search(filters=...)`
  - **复杂过滤**（price Range 等）：通过 `build_filter` 生成 Qdrant Filter 对象，使用 `_search_with_complex_filter` 直接调用 Qdrant client
  - **优先级**：当 `qdrant_filter` 存在时必须优先使用（包含 price 范围约束），不能跳过。否则 price_max 过滤形同虚设
  - **调试日志**：记录 `filter_built`（过滤条件）、`semantic_search`（无过滤结果）、`filtered_results`（过滤后结果）、`hybrid_search`（最终汇总）
- **产出**：
  - `src/retrieval/hybrid_retriever.py`：一步式混合检索（Qdrant search + Payload filter）
  - `src/retrieval/filter_builder.py`：LLM 输出 → Qdrant Filter 条件转换
  - 3 个 Mock 平台 API（含模拟延迟 200-800ms）
  - 模拟异常：5% 概率超时
  - 验证：输入"20 元以内奶茶"→ Qdrant 一步返回同时满足价格约束和语义相关的结果，无漏斗损失

### T2.4 多目标排序引擎
- **描述**：综合多维度对候选商品重排序
- **复杂度**：高
- **依赖**：T2.3
- **产出**：
  - `src/agents/ranker.py`
  - 排序维度：相关性/价格/口碑/时效/个性化
  - 权重配置（默认权重 + 用户偏好调整）
  - 加权融合算法
  - 排序结果可解释化（每个商品附带排序依据）
- **后过滤策略**（排序后执行，确保结果符合用户约束）：
  1. **场景过滤**（`src/agents/scenario_filter.py`）：根据用户场景（如"送礼物"）映射品类白名单/黑名单，剔除不适配品类。例如礼物场景排除 {奶茶, 食品, 家居}，仅保留 {护肤, 数码, 服饰, 运动}
  2. **预算过滤**：剔除 `final_price > price_max` 的商品（促销后价格仍超预算）
  3. **异常价格过滤**：剔除 `is_abnormal=True` 的商品（单价 1 元运费 50 元等陷阱）
  4. **执行顺序**：场景过滤 → 预算过滤 → 异常过滤（场景过滤先执行，减少后续处理量）

### T2.5 促销规则解析（3 种精心构造的促销类型）
- **描述**：实现 3 种促销类型，分别支撑工具调用、澄清引擎、安全防护三个架构亮点
- **复杂度**：中（简化后）
- **依赖**：T0.3a
- **三种促销设计**：
  - **类型 1：满减（基础算力型）**—— 满 100 减 20，支撑工具调用链路连通性，展示划线价→到手价
  - **类型 2：数量阶梯折扣（意图探测型）**—— 买 3 件打 7 折（单件原价），支撑澄清引擎主动追问"要不要多囤几件"
  - **类型 3：价格异常 / 运费陷阱（边界触发型）**—— 单价 1 元运费 50 元 / 历史价 199 元突然标 5 元，支撑安全边界检测 + HITL 拦截
- **架构设计**：promotion_calculator 为无状态独立节点，输入=结构化购物车 Payload，输出=最终价格。生产环境对接真实结算中台 API 无需改动 Agent 核心链路
- **产出**：
  - `src/tools/promotion_calculator.py`：促销计算工具（满减 / 数量折扣 / 异常检测）
  - `data/mock_promotions.json`：3 种促销规则数据
  - 验证：满减计算正确 + 澄清引擎主动追问 + 异常价格拦截

### T2.6 可解释推荐理由生成
- **描述**：为每个推荐结果生成个性化推荐理由
- **复杂度**：中
- **依赖**：T2.4
- **产出**：
  - `src/agents/explainer.py`
  - 结构化理由模板：`因为你[偏好匹配] + 这款[商品优势] + [价格/促销信息]`
  - LLM 润色层：自然语言化
  - 对比式推荐：两个候选接近时生成对比卡片
  - 反事实解释：回答"为什么没推荐 X"

### T2.7 完整推理链路集成
- **描述**：将 T2.1-T2.6 串联为完整的 LangGraph 图，对外暴露 SSE 流式接口供前端调用
- **复杂度**：高
- **依赖**：T2.1 ~ T2.6
- **延迟目标**（分层，非单一指标）：
  | 路径 | 触发条件 | 目标 P95 | 关键路径 |
  |------|----------|----------|---------|
  | 快速路径 | Semantic Router 命中 + 无澄清 | **~2.1s** | SR(5ms) ∥ Entity(800ms) ∥ Memory → Qdrant(5ms) → Ranker(1200ms) → Explainer(800ms) |
  | 标准路径 | LLM Router + 无澄清 | **~3.5s** | Router(800ms) + 同上 |
  | 澄清路径 | 含 1-2 轮追问 | **~5-8s** | 上述 + Clarification(800ms) × 轮数 |
  | 最差路径 | 慢系统 + 重试 + 多轮澄清 | **~10-12s** | 含 LLM 重试 + 模型降级 |
  - **统计口径**：加权平均 = 0.70×2.1 + 0.25×3.5 + 0.05×6.5 ≈ **2.8s**
  - **简历表述**：核心交互链路（快系统命中 + 无澄清）P95 ~2.1s，加权平均 ~2.8s
- **产出**：
  - `src/graph/shopping_graph.py`
  - 完整流程：意图识别 → 路由 → 混合检索 → 排序 → 解释 → 输出
  - SSE 流式输出接口（供前端 T3.6 调用）
  - 端到端可运行
  - 延迟分层统计：快速路径 / 标准路径 / 澄清路径分别打点

---

## Phase 3：执行层（Week 7-8）

> 目标：Skill 化封装 + 模拟下单

### T3.1 Skill Schema 定义
- **描述**：定义标准化的 Skill 接口规范
- **复杂度**：低
- **依赖**：无
- **产出**：
  - `src/skills/schema.py`
  - JSON Schema：`{name, description, parameters, permissions, endpoint, version}`
  - 读/写权限分类

### T3.2 Skill 注册中心
- **描述**：实现 Skill 的注册、发现、版本管理
- **复杂度**：中
- **依赖**：T3.1
- **产出**：
  - `src/skills/registry.py`
  - 注册/注销 API
  - 语义匹配发现：根据用户意图自动选择 Skill
  - 版本管理：支持灰度切换
  - Skill Schema 用 SQLModel 定义，注册即持久化

### T3.3 基础 Skill 实现（搜索/比价/详情）
- **描述**：实现 3 个核心读操作 Skill
- **复杂度**：中
- **依赖**：T3.2, T0.3a
- **产出**：
  - `src/skills/search_skill.py`
  - `src/skills/compare_skill.py`
  - `src/skills/detail_skill.py`
  - 每个 Skill 读取 Mock 数据，模拟真实 API 行为

### T3.4 下单流程 Skill + Human-in-the-Loop
- **描述**：利用 LangGraph 原生 interrupt_before + Checkpointer 实现 HITL，替代手写状态机
- **复杂度**：中
- **依赖**：T3.2, T2.2
- **HITL 机制**：
  - 图定义中 `interrupt_before=["order"]`，Agent 执行到下单节点前自动挂起
  - Checkpointer（MemorySaver / RedisSaver）自动持久化 State，零丢失
  - 前端展示确认页，用户点击"确认支付"后调用 `Command(resume={...})` 恢复执行
  - 扩展：所有敏感操作（下单/优惠券/修改地址/支付）都用 interrupt_before 保护
- **产出**：
  - `src/skills/order_skill.py`：下单逻辑（幂等性保证，SQLModel 持久化 Order 表）
  - `src/graph/hitl_nodes.py`：HITL 节点模板（通用 interrupt + resume 模式）
  - `src/graph/checkpointer.py`：可插拔 Checkpointer 封装（Memory / SQLite / Redis）
  - 前端确认页：展示订单信息 + 取消/确认按钮 + 5 分钟超时倒计时
  - 验证：Agent 执行到下单自动挂起 → 前端展示确认 → 用户确认 → 恢复执行 → 下单成功

### T3.5 安全边界 + 错误恢复与重试
- **描述**：实现四层安全防护 + 全链路错误恢复与重试策略
- **复杂度**：高
- **依赖**：T3.2, T2.7
- **四层安全防护**：
  - `src/security/input_guard.py`：输入安全
    - Prompt Injection 检测（关键词过滤 + 输出校验）
    - 输入长度限制（≤ 500 字符）
    - 意图白名单（只处理购物相关）
  - `src/security/output_guard.py`：推理安全（确定性规则校验，非 LLM 二次判定）
    - **幻觉检测（< 1ms，不增加 LLM 调用）**：
      - **坑点**：如果用 LLM 做 Self-RAG 比对（LLM 输出 vs 检索源），会增加 500-800ms 串行 LLM 调用，与 P95 ~2.1s 目标冲突
      - **解法**：强制 Ranker/Explainer 输出结构化溯源字段 `{"item_id": "p_123", "reason": "..."}`，Python 内存级断言校验 item_id 是否存在于 Hybrid Retriever 的返回列表中
      - **校验逻辑**：`assert item_id in {r.item_id for r in search_results}`，耗时 < 1ms
      - **价格边界**：`assert price >= historical_min * 0.3`（低于历史最低价 30% 触发告警）
      - **覆盖度校验**：`assert len(output_items) <= len(search_results)`（输出不能多于检索结果）
    - 来源标注：每个推荐的 item_id 必须可溯源到检索结果
  - `src/security/permission.py`：执行安全
    - 权限分级：read（自动）/ write（用户确认）/ sensitive（二次确认 + 验证）
    - 支付幂等性：同一请求多次执行只产生一笔订单
    - 金额校验：展示金额 vs 实际扣款金额一致性检查
    - 速率限制：单用户每分钟最多 10 次工具调用
  - `src/security/data_guard.py`：数据安全
    - 会话隔离：每个用户独立 State
    - 地址脱敏：展示时隐藏门牌号
    - 支付信息不存储：Agent 永远不接触卡号
    - 执行日志脱敏（logger 异步落盘，敏感字段自动脱敏）
- **错误恢复与重试策略**（四层分级）：
  - **LLM 层**：
    - 可重试：格式异常（JSON 解析失败）、429 限流、503 服务不可用、超时（>30s）
    - 策略：最多重试 3 次，指数退避（1s/2s/4s），第 3 次切换备用模型（如 Qwen→DeepSeek）
    - 不可重试：401 认证失败、内容安全拒绝 → 直接返回用户友好错误
    - 静默处理：格式异常重试对用户无感；429 限流自动等待 Retry-After
  - **Tool 层**：
    - 可重试：网络超时、连接重置、5xx 服务端错误
    - 策略：最多重试 2 次，固定间隔 500ms，降级到缓存数据或跳过该工具
    - 特殊处理：Qdrant 检索失败 → 降级到结构化 DB 查询（召回率下降但不阻塞）
    - 用户可见：工具持续失败时返回"商品数据加载中，请稍后再试"
  - **Memory 层**：
    - 可重试：Redis 连接超时、Qdrant 连接超时
    - 策略：最多重试 2 次，固定间隔 200ms，失败后降级为无记忆模式
    - 降级：L2b 摘要不可用 → 只用 L2a 滑动窗口；L2c 向量召回不可用 → 跳过历史注入
    - 静默处理：记忆层降级用户无感，仅影响个性化程度
  - **前端层**：
    - 网络错误：自动重试 3 次（1s/2s/4s），显示"网络不稳定，正在重试..."
    - SSE 断流：自动重连 + 从断点恢复（利用 Checkpointer）
    - 用户主动重试：重试按钮，重置输入状态
  - **错误分类**：
    - 静默错误（用户无感）：格式重试、记忆降级、Tool 缓存降级
    - 用户可见（有行动指引）："网络不稳定，请稍后重试"、"商品数据加载中"
    - 需用户介入："请重新描述您的需求"、"请确认支付信息"
  - **监控指标**：各层重试率、降级触发率、平均恢复时间、用户可见错误率
- **产出**：
  - `src/security/` 目录：四层安全模块
  - `src/resilience/llm_retry.py`：LLM 重试处理器（格式修复 + 模型降级 + 429 退避）
  - `src/resilience/tool_retry.py`：Tool 重试处理器（超时重试 + 缓存降级）
  - `src/resilience/memory_fallback.py`：记忆降级管理器（无记忆模式 + 渐进恢复）
  - `src/resilience/error_classifier.py`：错误分类器（静默/用户可见/需介入）
  - `config.yaml` 中增加 `resilience` 配置段（各层重试次数、超时、退避策略）

### T3.6 前端开发 + SSE 流式通信
- **描述**：开发对话式交互界面，实现 SSE 实时流式输出，包含消息流、商品卡片、HITL 确认弹窗
- **复杂度**：高
- **依赖**：T2.7, T3.4
- **SSE 实现方案**：
  - 后端：FastAPI StreamingResponse + LangGraph `astream`（stream_mode="updates"）
  - 前端：fetch + ReadableStream 消费 SSE 流（支持 POST，无需额外依赖）
  - 三种流式模式：token 级（逐字显示）、节点级（显示当前执行步骤）、工具调用级（显示正在查询什么）
  - HITL 续接：SSE 推送 interrupt 事件 → 前端弹确认框 → 用户确认后 resume 端点从 Checkpoint 恢复 → SSE 流无缝续接
  - 自定义 Hook：`useChatStream` 封装 SSE 解析、状态管理、中断恢复
- **产出**：
  - `frontend/` 目录：Next.js 14 App Router 项目
  - `frontend/src/hooks/useChatStream.ts`：SSE 流式通信 Hook（token 追加 + 节点状态 + HITL 中断）
  - `frontend/src/components/ChatBox.tsx`：对话主界面（消息列表 + 输入框 + 流式渲染）
  - `frontend/src/components/ProductCard.tsx`：商品卡片组件（图片/价格/推荐理由/左右滑动对比）
  - `frontend/src/components/OrderConfirm.tsx`：HITL 确认弹窗（订单信息 + 规格选择 + 超时倒计时）
  - `src/api/chat.py`：后端 SSE 端点（/api/chat + /api/chat/resume）
  - 技术栈：Tailwind CSS + shadcn/ui + Zustand + fetch ReadableStream
  - 响应式布局：桌面端 + 移动端适配

---

## Phase 4：深度优化 + 评测（Week 9-10）

> 目标：打磨技术亮点，建立评测体系

### T4.1 评测数据集构建
- **描述**：构建覆盖各场景的评测数据集
- **复杂度**：中
- **依赖**：无
- **产出**：
  - `data/eval/` 目录
  - 200 条测试用例，覆盖：简单搜索/多轮澄清/比价/改主意/歧义输入/边界 case
  - 每条用例含：输入、期望意图、期望推荐结果、期望追问次数

### T4.2 推荐准确率评测 + FAISS vs Qdrant 漏斗陷阱对比实验
- **描述**：量化推荐质量，重点验证 Qdrant 预过滤 vs FAISS 后置过滤在 5,000 条数据上的召回率差异
- **复杂度**：中
- **依赖**：T4.1, T2.7, T0.3b
- **召回率对比实验**（简历亮点 6 的数据支撑）：
  - **数据集**：T0.3b 生成的 5,000 条商品
  - **实验设计**：相同查询集（50 条，覆盖各品类 + 各价位段），分别用 FAISS Top-50 + Python 后置过滤 和 Qdrant 原生预过滤，对比召回率
  - **指标**：
    - Recall@50：FAISS 召回后过滤 vs Qdrant 预过滤
    - 空结果率：过滤后候选集为空的比例
    - P95 延迟：两种方案的检索延迟对比
  - **预期结果**：5,000 条数据上，FAISS 后置过滤召回率 40-70%，空结果率 > 15%；Qdrant 预过滤召回率 ~100%，空结果率 0%
  - **面试话术支撑**：用真实实验数据堵住"你凭什么说 FAISS 有漏斗陷阱"的追问
- **推荐准确率评测**：
  - 指标：准确率 / 召回率 / NDCG
  - 对比基线：纯 LLM 推荐 vs Agent 推荐（多目标排序 + 条件画像）
- **产出**：
  - `evals/eval_recommendation.py`：推荐准确率评测
  - `evals/eval_recall_comparison.py`：FAISS vs Qdrant 召回率对比实验
  - `reports/eval_recall_report.md`：实验结果报告（含对比表格 + 结论）
  - 验证：在 5,000 条数据上跑出真实对比数据，图表可直接用于简历和面试

### T4.3 需求澄清效率评测
- **描述**：量化澄清策略效果
- **复杂度**：中
- **依赖**：T4.1, T1.3
- **产出**：
  - `evals/eval_clarification.py`
  - 指标：平均追问轮数、候选集缩减率
  - 对比基线：固定模板追问 vs 信息熵追问

### T4.4 端到端性能评测（分层延迟）
- **描述**：量化系统延迟和稳定性，按路径类型分层统计
- **复杂度**：中
- **依赖**：T2.7
- **评测维度**：
  - **分层延迟**（非单一 P95）：
    - 快速路径（Semantic Router 命中 + 无澄清）：目标 P95 ~2.1s
    - 标准路径（LLM Router + 无澄清）：目标 P95 ~3.5s
    - 澄清路径（含追问）：目标 P95 ~5-8s
    - 加权平均 P95：目标 ~2.8s
  - **并行收益**：串行 vs 并行 DAG 执行延迟对比
  - **各节点耗时分布**：每个 Agent/Tool 的 P50/P95/P99 耗时
  - **降级触发率**：各层降级（LLM 模型降级、Tool 缓存降级、Memory 降级）的触发频率
- **产出**：
  - `evals/eval_performance.py`：分层延迟评测脚本
  - 指标：快速路径/标准路径/澄清路径分别统计 P50 / P95 / P99
  - 对比：串行 vs 并行 DAG 执行
  - 降级触发率统计

### T4.5 对话鲁棒性评测
- **描述**：测试意图切换、歧义输入、回退等边界场景
- **复杂度**：中
- **依赖**：T1.6, T4.1
- **产出**：
  - `evals/eval_robustness.py`
  - 指标：意图切换准确率、歧义消解准确率、对话完成率
  - 50 条边界 case 专项测试

### T4.6 Prompt 优化 + Token 成本控制
- **描述**：优化各环节 Prompt，降低 token 用量
- **复杂度**：中
- **依赖**：T4.2 ~ T4.5
- **产出**：
  - 优化后的 Prompt 模板
  - Token 用量对比：优化前 vs 优化后
  - 流式输出支持（SSE）

---

## Phase 5：简历包装（Week 11-12）

> 目标：提炼亮点，准备面试材料

### T5.1 架构图绘制
- **描述**：绘制系统架构图，展示整体设计
- **复杂度**：低
- **依赖**：T2.7
- **产出**：
  - 三层架构图（感知/推理/执行）
  - LangGraph DAG 流程图
  - 数据流图

### T5.2 技术决策文档
- **描述**：记录关键技术选型的理由和 trade-off
- **复杂度**：低
- **依赖**：全部
- **产出**：
  - `docs/decisions.md`
  - 覆盖：为什么选 LangGraph / 为什么用信息熵 / 为什么 DAG 而非串行
  - 每个决策含：背景、备选方案、选择理由、trade-off

### T5.3 STAR 话术整理
- **描述**：整理 5 个技术亮点的面试话术
- **复杂度**：低
- **依赖**：T4.2 ~ T4.5
- **产出**：
  - 每个亮点：S/T/A/R 各 2-3 句话
  - 准备 3 个深度追问的回答
  - 量化指标与实验方法论

### T5.4 Demo 视频录制
- **描述**：录制 2 分钟端到端演示视频
- **复杂度**：中
- **依赖**：全部
- **产出**：
  - 完整流程演示：输入 → 澄清 → 推荐 → 下单
  - 边界 case 演示：改主意 / 歧义输入
  - 展示并行 DAG 执行的延迟优势

### T5.5 GitHub README 打磨
- **描述**：项目首页文档，决定第一印象
- **复杂度**：低
- **依赖**：T5.1, T5.2
- **产出**：
  - 项目简介 + 架构图
  - 技术栈 + 核心亮点
  - Quick Start 指引
  - Demo GIF / 视频链接

---

## 里程碑节点

| 里程碑 | 时间 | 验证标准 |
|--------|------|----------|
| **M0：最小闭环** | Week 1 末 | 命令行输入"帮我找奶茶"，返回 3 个推荐结果 |
| **M1：智能对话** | Week 3 末 | 能多轮澄清需求，歧义输入能正确消歧 |
| **M2：完整推理** | Week 6 末 | 多平台比价 + 多目标排序 + 可解释推荐全流程跑通 |
| **M3：端到端 Demo** | Week 8 末 | 从对话到模拟下单完整链路可演示 |
| **M4：评测达标** | Week 10 末 | 5 个量化指标全部产出，有对比基线 |
| **M5：简历就绪** | Week 12 末 | STAR 话术 + 架构图 + Demo 视频 + GitHub README 全部完成 |

---

## 依赖关系图

```
T0.1 ──┬──→ T0.2 ──→ T2.2 ──→ T2.3 ──→ T2.4 ──→ T2.6 ──→ T2.7 ──→ T3.6
       │                        ↑                                  ↑
       ├──→ T0.3a ──→ T0.6 ──→ T2.3                               │
       │          └──→ T3.3                                        │
       ├──→ T0.3b ──→ T4.2（漏斗陷阱对比实验）                    │
       │                                                           │
       ├──→ T0.5 ──→ T1.1 ──→ T1.3 ──→ T1.6                      │
       │         └──→ T1.2 ──→ T1.3                               │
       │         └──→ T1.5                                        │
       │                                                           │
       ├──→ T0.4（独立可运行）                                     │
       │                                                           │
       ├──→ T0.7（日志基础设施，被所有后续模块依赖）               │
       │                                                           │
       ├──→ T3.1 ──→ T3.2 ──┬──→ T3.3                            │
       │                    ├──→ T3.4 ─────────────────────────────┘
       │                    └──→ T3.5
       │
       └──→ T4.1 ──→ T4.2 ~ T4.5 ──→ T4.6 ──→ T5.3
```

## 复杂度分布

| 复杂度 | 任务数 | 任务编号 |
|--------|--------|----------|
| 低 | 6 | T0.1, T0.2, T3.1, T5.1, T5.2, T5.5 |
| 中 | 21 | T0.3a, T0.3b, T0.4, T0.5, T0.6, T0.7, T1.1, T1.2, T1.4, T2.1, T2.5, T2.6, T3.2, T3.3, T3.4, T4.1~T4.6, T5.4 |
| 高 | 9 | T1.3, T1.5, T1.6, T2.2, T2.3, T2.4, T2.7, T3.5, T3.6 |
