# ShopMesh 后续路线图

> 汇总本阶段已完成项 + 剩余任务,供排期。更新日期见 git。

## 已完成(本阶段)

- 环境搭建:Docker infra(shop-agent-*)、Ollama+bge-m3、venv、.env、向量索引、端口规整(pg 15432)
- P0-1 商品数据语义化重建(子品类价格区间 + 图片)
- P0-2 三个核心过滤 bug(预算/场景/品类)+ 15 条回归测试
- P0-3 流式进度 + 重试按钮 + **中间件缓冲修复**(BaseHTTPMiddleware→纯 ASGI)
- 登录修复(auth 响应 camelCase)、数据源统一(load_products→5k)
- P1-1 架构收敛:删死代码、单一执行路径、移除 orchestrator DAG
- 延迟:P-1 逐商品文案确定性化、P-2 收敛 agent 冗余工具调用、P-3 summary 模板化、P-4 意图/实体规则快路径(实测 116s→22–39s)
- **购物功能 P1–P4**:购物车 / 订单(确定性 commit·库存·幂等·状态机)/ 支付(签名 webhook·超时取消)/ 退款·订单历史
- **Eval 缺陷闭环(72.4%→96.6%)**:① 礼物场景无锚点召回打空 → 场景品类白名单下推到 Qdrant 预过滤(`filter_builder`);② 无解预算返回空 → `product_search` 检测空结果时确定性放宽(护 product_type/category 不动)+ 最接近(最便宜)替代 + 用户可见放宽提示;附带修 `ask_clarification` search_failed 分支 NameError。回归测试 `tests/test_recall_relaxation.py`(13 条)。

---

## 剩余任务

### A. 延迟
- **P-2 收敛 agent 冗余工具调用** ✅ — 已完成(commit 31648a3)。
- embedding 瓶颈(bge-m3 CPU ~9s/次)⬜ — semantic_router + 记忆召回仍各含一次;可规则快路径再跳过,或 GPU/小模型(基建)。注:放宽重检索已复用 embedding(`hybrid_search(query_vector=...)`),不再二次 embed。

### B. 质量 / 可靠性
- **Eval harness** ✅ — `scripts/eval_recommendation.py`,现含「优雅降级」指标(无解约束时以带标注放宽替代空结果)。
- **Eval 缺陷闭环** ✅ — 见「已完成」。72.4%→96.6%。
- agent 工具调用稳定性 ⬜ — 有时跳过 product_search / 过度调用(与 P-2 同源),prompt + 循环收敛。
- test_tools.py 4 个既有失败 ✅ — 均为测试对旧 API 的漂移(硬编码 6 工具 / `questions` 键 / category-only 放宽),已更新为断言当前正确行为(子集断言工具、question_spec 结构、空实体无可放宽)。现 20/20。

### C. 购物功能延伸(P1–P4 已完成)
- 真实支付接入(mock→支付网关 hosted checkout;webhook 验签逻辑可复用)⬜
- 履约状态(shipped→completed)、收货地址、购物车落 Postgres 持久化 ⬜

### D. 工程收尾
- 静默异常治理(auth/memory 的 `except: pass`)—— Tier 1 ✅(吞异常改为 debug/warning 可观测;apikey commit 失败补 rollback);Tier 2 ✅(审计全部 67 处 `except Exception`:绝大多数是 LLM/Qdrant/DB/工具执行的**有意 resilience 边界**,收窄反而降鲁棒性,保留;仅收窄 4 处纯本地操作 —— normalizer/ranker 的 yaml 载入→`(OSError, yaml.YAMLError)`、tool_executor 日志抽取→形状错误、knowledge_base 促销 json 载入→`(OSError, json.JSONDecodeError)`,让真 bug 浮出)
- filter_builder 旧时尚 taxonomy 死映射 ✅ — 删除 `_expand_category` 整条嵌套 taxonomy 链(`_PRODUCT_TYPE_MAP`/`_BROAD_CATEGORY_MAP`/`_semantic_type_match` 等,现库为扁平品类,该链在检索路径已死);与 clarification_router 无真实耦合(仅同名 `_PRODUCT_TYPE_MAP`,各自独立)。清理 test_p8 里对应的过时用例。
- **双迁移 source of truth**(Flyway vs Alembic 定一个)✅ — Flyway 唯一所有者;Alembic(`alembic/`+`alembic.ini`+`scripts/migrate.py`)已删,`a1c2` 补成 Flyway `V5`。见 [docs/MIGRATIONS.md](../docs/MIGRATIONS.md)。
- README / 简历叙事:过度设计反思 + 架构收敛 + 生产级 agent 交易设计 ✅ — README 重写:工程亮点前置(收敛/延迟 116s→22-39s/交易设计/eval 72.4%→96.6%/迁移收敛)+ 诚实的过度设计反思 + 准确的双语言架构与可运行的启动步骤。简历话术见 [docs/resume-narrative.md](../docs/resume-narrative.md)。

### E. 运行提醒(非 bug)
- 历史 / 订单超时取消需 Celery worker + beat 在跑。
- 改后端需重启 uvicorn(或用 `--reload`)。

---

## 建议顺序(Top）

> 工程收尾全部完成:Eval 缺陷闭环、静默异常 Tier 1+2、双迁移收敛、filter_builder 死映射、test_tools 修复、README 架构叙事。

剩余均为可选,按需推进:
1. **agent 工具调用稳定性** — 偶发跳过/过度调用 product_search;prompt + 循环收敛。依赖 LLM 网关(不稳),验证成本高。
2. **购物功能延伸** — 真实支付 hosted checkout / 履约状态 / 收货地址 / 购物车落 Postgres。功能扩展,非收尾。
3. **embedding 瓶颈** — bge-m3 CPU ~9s;需 GPU 或小模型(基建活)。
