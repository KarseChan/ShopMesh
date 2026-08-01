# 延迟优化方案(对话导购在线路径)

> 版本 v1 · 基于本会话实测数据 + 生产级架构对比(淘宝 RecGPT / 美团 LongCat 公开资料)
> 目标:把单次导购从实测 **116s** 压到 **~15-25s**(感知延迟 <3s,靠流式首 token)

---

## 1. 实测基线(profiler,查询「推荐几双800元以内的男士跑步鞋」)

单次请求 **116.4s = 8 次串行 LLM 调用**:

| 阶段 | 耗时 | LLM 次数 | 说明 |
|------|------|---------|------|
| preprocess(意图+实体) | 20.5s | 1 | 大模型抽实体 in461/out471 |
| agent ReAct 循环 | 35s | 3 | product_search + review_summary×2(**后两次冗余**) |
| narrative 逐商品文案 | 47s | 3 | 每个商品单独一次 LLM 写介绍(out~800) |
| summary 收尾 | 14s | 1 | 生成总结 out642 |

**根因**:LLM 全在同步请求路径里串行跑;而生产级做法是「LLM 离线富化 + 快基建在线服务」(RecGPT 三阶段离线生成标签注入传统召回排序,在线数十 ms)。

**指导原则**:①在线 LLM 调用次数 8→1~2;②能确定性/预计算的绝不在线调 LLM;③首 token 尽快出、全程流式;④不追生产级基建(自研 MoE/GPU/投机解码),那是钱和团队堆的。

---

## 2. 优化项(按 ROI 排序)

### P-1 逐商品文案确定性化 ⭐最高优先
- **问题**:narrative 每个商品一次 LLM 写介绍(3 次,~45s)。
- **方案**:`stream_narrative` 的逐商品循环改用排序器已算好的 `rank_reason_text` 组装文案(SSE 事件不变,前端零改),删 `_build_product_intro_messages`。
- **文件**:`src/graph/stream_utils.py`
- **预计**:−45s。**风险低**(rank_reason_text 已验证有值,有回退)。
- **状态**:改动清单已就绪,待执行。

### P-2 收敛 agent 冗余工具调用 ⭐
- **问题**:agent 在 ReAct 循环里重复/多余调用工具(实测 review_summary×2;截图里追问×2/检索×2)。既慢又导致 UI「×2」。
- **方案**:①agent prompt 明确「推荐场景不必调 review_summary;拿到结果即产出,勿重复同一工具」;②`should_continue` / tool_executor 加「同一 (tool,args) 连续重复调用」拦截 + 每类工具单轮上限;③前端 `tool_call` 按 (tool+args) 去重(治标)。
- **文件**:`src/graph/specialized_agents.py`(prompt/should_continue)、`src/graph/tool_executor.py`、`frontend/src/hooks/useChatStream.ts`
- **预计**:−10~25s + 修掉 Q1 重复展示。**风险中**(改循环终止逻辑需回归测试)。

### P-3 summary 轻量化
- **问题**:收尾总结 1 次 LLM,~14s。
- **方案**:模板化(「为你精选 N 款,¥x~¥y,均在预算内」)或改用快模型(见 P-6)。
- **文件**:`src/graph/stream_utils.py`
- **预计**:−10~14s。**风险低**(措辞略平)。

### P-4 意图/实体抽取提速
- **问题**:preprocess 用大模型抽实体 ~20s。
- **方案**:①简单查询走规则/小模型分类(category_detector 已是关键词法,可扩到实体);②大模型抽取仅在规则低置信时兜底;③常见 query 结果缓存(Redis)。
- **文件**:`src/graph/preprocessing.py`、`src/agents/entity_extractor.py`、`src/router/`
- **预计**:−10~18s。**风险中**(规则覆盖不全会降准,需保留 LLM 兜底)。

### P-5 模型分层(若有快模型端点)⭐潜在最大杠杆
- **问题**:根因是 product-group 推理模型 7-15s/次,所有步骤都用它。
- **方案**:config 支持多模型;把**简单步骤**(实体抽取、narrative 文案、summary)指向**快的非推理模型**,只把**难推理**(复合意图、比价决策)留给推理模型。
- **文件**:`config.yaml`(llm 多档)、`src/models/llm_client.py`、各调用点 `get_llm("fast"/"reason")`
- **预计**:若快模型 1-2s/次,可整体再砍 50%+。**风险低**,**依赖是否有快模型端点**。

### P-6 LLM 离线富化 + 缓存(架构级,选做)
- **问题**:文案/理由/画像在线现算。
- **方案**:对齐 RecGPT 思路——离线(Celery 批任务)预生成商品卖点文案、用户画像标签并缓存;在线直接取。
- **文件**:`src/tasks/`(新增批任务)、`conversation_store`/`profile` 读缓存
- **预计**:把"文案生成"从在线彻底移走。**风险中**,工作量较大,demo 阶段可后置。

### P-6' 并行化独立子任务
- narrative 三个商品的文案(若仍需生成)本相互独立,可并行而非串行;preprocess 的意图/实体/记忆召回已部分并行,检查是否充分。
- **预计**:并行 3 路 ~ 省 2/3 该段时间。

---

## 3. 目标延迟预算(做完 P-1~P-4)

| 阶段 | 现状 | 目标 |
|------|------|------|
| 意图+实体 | 20.5s | 3-5s(P-4) |
| 检索+排序 | ~12s(1 次 LLM 决策 + embedding + qdrant) | 5-8s |
| 文案+总结 | 61s | 2-4s(P-1 确定性 + P-3 模板/快模型) |
| 冗余调用 | 20s+ | 0(P-2) |
| **合计** | **116s** | **~15-25s**;首 token <3s(流式) |

---

## 4. 执行进度与顺序

- ✅ **P-1**(逐商品文案确定性化)—— 完成(f9b408f)。narrative 逐商品段 ~45s → 0s。
- ✅ **P-3**(summary 模板化)—— 完成(805cf8c)。至此 **narrative 段整体 ~61s → 0.002s**,去掉 4 次 LLM。
- ❌ **P-5**(模型分层)—— **不可行**:网关只暴露 `product-group` 单一慢推理模型(实测 `/v1/models` 仅一个),没有更快模型可切。
- ✅ **P-4**(意图/实体规则快路径)—— 完成(f42d92e)。**简单首轮 query preprocess ~20s → ~0.05s**。
  - 关键发现:preprocess 真瓶颈不是实体 LLM,而是 **bge-m3 CPU embedding 单次 ~9s**,semantic_router + 记忆召回各含一次。规则命中时把这两步一起短路。
- ⬜ **P-2**(收敛 agent 冗余工具)—— 减少 agent 迭代次数;同时修 Q1 展示重复。**现在的主要剩余延迟**:agent 步的 1 次检索 embedding(~9s)+ 1-3 次 agent LLM(~15s/次)。
- ⬜ **P-6 / P-6'**(离线富化 / 并行 / embedding 缓存)—— 架构级,按需。embedding ~9s 也可靠缓存高频 query 向量缓解。

> 累计效果(简单首轮 query,预热后):**preprocess 20s→0.05s(P-4)+ narrative 61s→0s(P-1/P-3)**。
> 请求总时长预计 116s → ~25-30s,剩余几乎全在 agent 步(检索 embedding + agent LLM 决策)。

> 关键前提变化:网关**只有一个慢模型**,所以降延迟的唯一手段是**减少在线 LLM 调用次数**(P-1/P-3 已各去掉整段;P-4/P-2 继续去)。

---

## 5. 明确不做

- 不自建 MoE / GPU 推理 / 投机解码 / 蒸馏小模型 —— 生产级基建,非 demo 范畴。
- 面试叙事重点是「我实测了延迟分布、定位到 8 次串行 LLM、并据生产级『LLM 离线富化』原理做收敛」,而非「追平美团响应速度」。
