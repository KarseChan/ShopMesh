# ShopMesh 后续路线图

> 汇总本阶段已完成项 + 剩余任务,供排期。更新日期见 git。

## 已完成(本阶段)

- 环境搭建:Docker infra(shop-agent-*)、Ollama+bge-m3、venv、.env、向量索引、端口规整(pg 15432)
- P0-1 商品数据语义化重建(子品类价格区间 + 图片)
- P0-2 三个核心过滤 bug(预算/场景/品类)+ 15 条回归测试
- P0-3 流式进度 + 重试按钮 + **中间件缓冲修复**(BaseHTTPMiddleware→纯 ASGI)
- 登录修复(auth 响应 camelCase)、数据源统一(load_products→5k)
- P1-1 架构收敛:删死代码、单一执行路径、移除 orchestrator DAG
- 延迟:P-1 逐商品文案确定性化、P-3 summary 模板化、P-4 意图/实体规则快路径(实测 116s→47s)
- **购物功能 P1–P4**:购物车 / 订单(确定性 commit·库存·幂等·状态机)/ 支付(签名 webhook·超时取消)/ 退款·订单历史

---

## 剩余任务

### A. 延迟
- **P-2 收敛 agent 冗余工具调用** ⬜ — 最大剩余杠杆。agent 多调 review_summary(实测 ×2,~20s),47s→~27s,并修 UI"×2"展示。
- embedding 瓶颈(bge-m3 CPU ~9s/次)⬜ — semantic_router + 记忆召回仍各含一次;可规则快路径再跳过,或 GPU/小模型(基建)。

### B. 质量 / 可靠性
- **Eval harness** ⬜ — 量化推荐命中率、工具调用正确率、错误恢复成功率、混合检索 vs 纯向量 recall。**最大差异化点**,基于已有回归测试扩展。
- agent 工具调用稳定性 ⬜ — 有时跳过 product_search / 过度调用(与 P-2 同源),prompt + 循环收敛。
- test_tools.py 4 个既有失败 ⬜ — `test_all_six_tools_registered` 硬编码 6 工具(现 8+);ask_clarification/constraint_relaxation 漂移。

### C. 购物功能延伸(P1–P4 已完成)
- 真实支付接入(mock→支付网关 hosted checkout;webhook 验签逻辑可复用)⬜
- 履约状态(shipped→completed)、收货地址、购物车落 Postgres 持久化 ⬜

### D. 工程收尾
- 静默异常治理(auth/memory 的 `except: pass`)⬜
- filter_builder 旧时尚 taxonomy 死映射(先与 clarification_router 解耦)⬜
- **双迁移 source of truth**(Flyway vs Alembic 定一个)⬜
- README / 简历叙事:过度设计反思 + 架构收敛 + 生产级 agent 交易设计 ⬜

### E. 运行提醒(非 bug)
- 历史 / 订单超时取消需 Celery worker + beat 在跑。
- 改后端需重启 uvicorn(或用 `--reload`)。

---

## 建议顺序(Top）

1. **P-2 收敛 agent 冗余调用** — 具体、可验证、一箭三雕(延迟+可靠+UI);更稳的 agent 也让后续 eval 测量更可靠。
2. **Eval harness** — 面试杀手锏,数据驱动。
3. **README 架构叙事** — 把成果讲清楚,否则做再多也传达不出。
