# 新窗口交接提示词

> 复制下面整段,粘贴到新的 Claude Code 窗口(工作目录已是 ShopMesh)作为第一条消息。

---

这是我的秋招项目 ShopMesh(基于 LangGraph 的对话式导购 + 交易 Agent)。上个会话很长,这里是交接。请先读 `CLAUDE.md`、`reports/roadmap.md`、`docs/resume-narrative.md` 了解现状,不要重复已完成的工作。

## 已完成(都已 commit 在 master)
- 环境:Docker infra(容器名 `shop-agent-*`:postgres**宿主端口 15432**、redis 16379、qdrant 16336、rabbitmq 5672)+ 宿主机 Ollama(bge-m3, 11434)+ `.venv` + `.env`。向量索引 collection=`products`(5000)与 `intent_samples`(77)已建。
- P0:商品数据语义化重建(带图)、三个核心过滤 bug(预算/场景/品类)+ 15 条回归测试(`tests/test_p02_core_filters.py`)、流式进度+重试、登录修复、数据源统一到 5k。
- 架构收敛:删死代码 + 收敛为**单一执行路径**(确定性路由→每意图 ReAct agent)、**移除 orchestrator DAG**。
- 延迟:116s→22–39s(P-1 逐商品文案确定性化 / P-3 summary 模板化 / P-4 规则快路径抽实体 / P-2 收敛冗余工具)。**网关只有一个慢推理模型 product-group,换不了,只能减少 LLM 调用次数。**
- 购物功能 P1–P4:购物车(Redis)+ 订单(确定性 commit/库存原子预占/幂等/状态机)+ 支付(沙箱,HMAC 验签 webhook + Celery 超时取消)+ 退款/订单历史 + 前端购物车/结算 UI。
- Eval:`scripts/eval_recommendation.py`(推荐质量量化,首跑 72.4%)。

## 怎么跑(三个独立终端 + 依赖)
```
docker compose -f docker-compose.infra.yml up -d        # 依赖容器
# 后端:$env:PYTHONPATH="."; .venv\Scripts\python.exe -m uvicorn src.api.chat:app --port 9000 --reload
# worker(历史/超时取消需要):.venv\Scripts\python.exe -m celery -A src.tasks worker -Q memory,cleanup,default,events --pool=solo
# 前端:cd frontend; npm run dev   → http://localhost:3000  登录 demo/demo1234
```

## 已知坑(别再踩)
- LLM 网关 key 会失效/限流(表现为 401 或 "AI 服务暂时不可用"),`.env` 的 `LLM_API_KEY`;网络到外网也不稳,别用会重试到超长的完整 pipeline 做诊断,用带死超时的单次调用。
- Windows 上 `localhost` 解析到 IPv6,Ollama 只绑 IPv4 → config 里用 `127.0.0.1`。
- DB schema 现由 **Flyway 唯一管理**(Alembic 已删)。改表 = 新增 Flyway `V{n}__*.sql`(Java 侧),Java 启动自动迁移;Python 只读写不迁移。见 [docs/MIGRATIONS.md](../docs/MIGRATIONS.md)。(dev 库残留的 `alembic_version` 表无害,可忽略。)
- bge-m3 在 CPU 上单次 embedding ~9s,是 preprocess/检索的硬地板。
- 改后端要重启 uvicorn(除非 --reload)。

## 下一步(见 reports/roadmap.md,按优先级)
1. ✅ 已完成:Eval 缺陷闭环。无解预算 → `product_search` 空结果时确定性放宽(护 product_type/category)+ 最便宜替代 + 放宽提示;礼物类无锚点召回 → 场景品类白名单下推 Qdrant 预过滤。72.4%→96.6%,回归测试 `tests/test_recall_relaxation.py`。
2. 工程收尾:静默异常治理(auth/memory 的 `except: pass`)、双迁移定 source of truth。
3. 可选:真实支付接入(mock→hosted checkout)、履约状态、收货地址。

我这次想做:【在这里写你要做的】
