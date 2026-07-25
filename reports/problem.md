# 问题记录

## P0: 预算过滤失效

**发现时间**: 2026-05-16

**现象**: 用户说 "预算 500 以内"，但推荐结果包含 ¥549 的商品。

**根因**: 两层过滤都没有生效。

1. `hybrid_retriever.py` 的 `_filter_to_dict` 只处理 category/brand，不处理 price → `simple_filters=null`
2. `build_filter` 虽然生成了 Qdrant Range filter（price ≤ 500），但 `hybrid_search` 只在 `results` 为空时才使用复杂过滤 → 有结果就跳过
3. 排序节点没有后过滤，超预算商品直接进入推荐

**解决方案**:

1. 修复 `hybrid_search` 逻辑 — 当 `qdrant_filter` 存在时优先使用复杂过滤（包含 price Range），不再仅在无结果时才尝试
2. 在 `node_rank` 加后过滤 — 排序后剔除 `final_price > price_max` 的商品和 `is_abnormal=True` 的商品

```python
# 修复后：始终使用 qdrant_filter
if qdrant_filter:
    results = await _search_with_complex_filter(col, query_vector, qdrant_filter, top_k)
elif simple_filters:
    results = await store.search(..., filters=simple_filters)
else:
    results = unfiltered_results
```

---

## P1: 礼物场景推荐品类不适配

**发现时间**: 2026-05-16

**现象**: 用户说 "送女朋友生日礼物"，推荐了蜜雪冰城柠檬水、喜茶、农夫山泉等日常饮品，不适合作为礼物。

**根因**: 系统把 "送礼物" 当普通搜索处理，缺少场景→品类映射层。实体提取出了 `scenario: "生日"`，但这个信息没有传递给检索和排序环节。排序维度中也没有 "礼物适合度"，奶茶靠 price=1.0 + reputation=1.0 冲到 Top 1。

**解决方案**:

1. 新增 `src/agents/scenario_filter.py` — 场景→品类白名单/黑名单映射
   - 礼物场景：允许 {护肤, 数码, 服饰, 运动}，排除 {奶茶, 食品, 家居}
2. 在 `node_rank` 后过滤中调用 `filter_by_scenario` — 排序后剔除不适配品类
3. 场景过滤在预算过滤之前执行，减少后续处理量

---

## P2: 混合检索未生效品类过滤（已修复）

**发现时间**: 2026-05-16

**现象**: 搜索 "帮我找化妆品" 返回了蜜雪冰城、瑜伽垫、喜茶等完全不相关的商品。实体提取正确识别了 `category: "护肤"`，但检索阶段没有使用该条件过滤。

**根因**: `src/retrieval/hybrid_retriever.py:70` 的条件逻辑写反了。当 `build_filter` 返回 Qdrant Filter 对象时，`filters` 参数被设为 `None`，导致纯语义搜索无品类约束。

**解决方案**: 修复 `hybrid_retriever.py`，始终将 entities 的简单过滤条件传给 `store.search()`。

**状态**: 已修复

---

## P3: LLM 返回纯文本导致 JSON 解析失败（已修复）

**发现时间**: 2026-05-16

**现象**: `explainer.py` 的 `polish_reason` 连续 3 次调用 `llm.chat_json()` 失败。

**根因**: 原 LLM 模型（`astron-code-latest`，讯飞 API）指令遵循能力不足，无法稳定输出 JSON。

**解决方案**: 切换为 DeepSeek（`deepseek-v4-flash`），JSON 输出稳定。

**状态**: 已修复

---

## P4: uvicorn 服务器返回 500（已修复）

**发现时间**: 2026-05-16

**现象**: API 返回 500，但 ASGI 直连正常。

**根因**: uvicorn 进程启动时未加载 `.env`，`LLM_API_KEY` 为空。

**解决方案**: 重启 uvicorn，确保从项目根目录启动。

**状态**: 已修复

---

## P5: 歧义词消歧链路失效（已修复）

**发现时间**: 2026-05-17

**现象**: 用户说 "我要苹果"，entity extractor 正确标记 `ambiguous: true`，但 disambiguator 没有消歧，clarification engine 回复泛化的 "你想找什么类型的商品？"，而非 "你说的'苹果'是指数码（Apple iPhone）还是食品（水果）？"

**根因**: 三层问题叠加。

1. `disambiguator._resolve_field` 当 `value=None`（LLM 标记歧义但无法赋值品类）时直接 `continue` 跳过消歧 → 永远无法解析
2. `shopping_graph.node_extract_entities` 仅在 `disambig_result["resolved"]==True` 时更新 entities → 消歧失败时，disambiguator 生成的 `_disambiguation_question` 被丢弃
3. `_get_catalog_candidates` 仅做精确匹配 → 用户输入 "我要苹果" 无法匹配静态映射 key "苹果"
4. `clarification_engine._build_disambiguation_question` 无候选信息，只能生成泛化问题

**解决方案**:

1. `_resolve_field` 新增 `raw_query` 参数 — 当 `value=None` 时用原始用户输入查找候选
2. `_get_catalog_candidates` 新增子串匹配 — 当精确匹配失败时，遍历 `_AMBIGUOUS_TERM_MAP` 做 `term in value` 子串匹配
3. `_AMBIGUOUS_TERM_MAP` 静态映射 — 为常见歧义词（苹果/小米等）提供候选品类 + hint
4. `node_extract_entities` 始终采用 disambiguator 返回的 entities — 不论 resolved 成功或失败
5. `disambiguate` 在消歧失败时将问题写入 `entities["_disambiguation_question"]`
6. `clarification_engine._build_disambiguation_question` 优先使用 `_disambiguation_question`，无则回退泛化问题
7. `_resolve_via_llm` 增加 `result` 空值检查 — 防止 `chat_json()` 返回 None 时 `.get()` 崩溃

**状态**: 已修复

---

## P6: Reputation 排序维度失效（已修复）

**发现时间**: 2026-05-17

**现象**: 用户说 "帮我找护肤品，要口碑好的"，排序结果与纯价格排序无差异。某杂牌精华液 9.9 元排第 1，SK-II（口碑最好）排最后。reputation 维度权重未提升，分数用 stock 做代理（0-0.128 区间，几乎无区分度）。

**根因**: 三层问题叠加。

1. **Entity Extractor 不捕获偏好信号** — "口碑好" 没有被提取为任何字段，`_adjust_weights` 无法感知用户偏好
2. **Ranker 缺少 reputation 偏好逻辑** — `_adjust_weights` 只处理价格敏感和礼物场景，无 reputation 偏好分支
3. **`_score_reputation` 用 stock 做代理** — mock 数据无 `reputation` 字段，stock 值域 0-9999 且与口碑无关（蜜雪冰城 stock=9999 但 reputation 不高）
4. **Qdrant payload 缺少 reputation** — `build_index.py` 索引时未包含 reputation 字段

**解决方案**:

1. `data/mock_data.json` — 给所有 30 个商品添加 `reputation` 字段（0-1，基于品牌声望：SK-II=0.92, 雅诗兰黛=0.84, 某杂牌=0.10）
2. `scripts/build_index.py` — payload 构造中加入 `reputation` 字段，重新索引 Qdrant
3. `src/agents/ranker.py` → `_score_reputation` — 优先使用 `product["reputation"]`，无则 fallback 到 stock 代理
4. `src/agents/ranker.py` → `_adjust_weights` — 新增 preference 偏好分支：当 entities 包含 "口碑/销量/大牌/知名/品牌" 时，reputation 权重 +0.15，price 和 timeliness 各 -0.075
5. `src/agents/entity_extractor.py` — SYSTEM_PROMPT 增加 `preference` 字段定义，提取用户偏好关键词（"口碑好"、"便宜"、"轻薄" 等）

**验证结果**:

| 排名 | 修复前（rep 权重 0.15, stock 代理） | 修复后（rep 权重 0.30, reputation 字段） |
|------|------|------|
| 1 | 某杂牌 ¥44.9 (rep=0.05) | 雅诗兰黛 [jd] ¥549 (rep=0.84) |
| 2 | 雅诗兰黛 [jd] ¥549 (rep=0.128) | 雅诗兰黛 [tb] ¥549 (rep=0.84) |
| 3 | 雅诗兰黛 [tb] ¥549 (rep=0.085) | 雅诗兰黛 [pdd] ¥549 (rep=0.84) |
| 4 | 雅诗兰黛 [pdd] ¥549 (rep=0.042) | 兰蔻 [jd] ¥830 (rep=0.82) |
| 5 | 兰蔻 [jd] ¥830 (rep=0.067) | SK-II [jd] ¥1490 (rep=0.92) |
| 6 | SK-II [jd] ¥1490 (rep=0.0) | 某杂牌 ¥44.9 (rep=0.10) |

杂牌从第 1 掉到最后，SK-II 从最后升到第 5。

**状态**: 已修复

---

## P7: 服装品类搜索失败 + Agent 幻觉推荐（已修复）

**发现时间**: 2026-05-18

**现象**: 用户提问"送男生衬衫"，Agent 回复推荐了 Nike Air Force、Adidas 三叶草等不在 mock_data 中的商品（幻觉），且前端显示调用了 `constraint_relaxation`（放宽条件）。

**根因**: 三层问题叠加。

1. **品类精确匹配失败** — 实体抽取器输出 `category: "服饰"`，但 mock_data 中品类为 `"男装/上装/T恤衬衫"` 等层级格式。`filter_builder.py` 使用 `MatchValue` 精确匹配，"服饰" ≠ "男装/上装/T恤衬衫" → 搜索结果为空
2. **Agent 约束放宽后未重试搜索** — ReAct Agent 调用 `constraint_relaxation` 后拿到放宽的 entities，但没有重新调用 `product_search`，而是直接生成了 Final Answer → LLM 凭空编造商品
3. **Qdrant 索引未重建** — mock_data 从护肤品更换为服装后，向量索引仍为旧数据

**解决方案**:

1. **品类前缀匹配** — `src/retrieval/filter_builder.py` 新增 `_expand_category()` 函数，支持层级品类前缀展开：
   - `"服饰"` → 匹配所有 `男装/...` + `女装/...` 子品类
   - `"男装"` → 匹配 `男装/上装/...` + `男装/下装/...`
   - `"男装/上装"` → 匹配 `男装/上装/...`
   - 使用 `MatchAny` 传入展开后的品类列表
2. **实体抽取器品类细分** — `src/agents/entity_extractor.py` SYSTEM_PROMPT 新增服饰细分提示，引导 LLM 输出 `"男装/上装"` 或 `"女装/下装"` 等更精确的品类
3. **Agent 约束放宽后强制重试** — `src/agents/react_prompt.py` 决策规则新增："拿到返回的 entities 后，必须立即用它重新调用 product_search"
4. **防幻觉规则** — `src/agents/react_prompt.py` 新增："严禁凭空编造商品信息，所有推荐必须基于工具返回的真实数据"
5. **工具描述强化** — `src/tools/agent_tools.py` constraint_relaxation 描述新增："重要：拿到返回的 entities 后，必须立即用它重新调用 product_search"
6. **重建 Qdrant 索引** — 删除旧 collection，用 `scripts/build_index.py` 重新索引 50 条服装数据

**状态**: 已修复

---

## P8: "服饰"大品类搜索零结果 — 品类体系不匹配（已修复）

**发现时间**: 2026-05-19

**现象**: 用户提问"我想买一件适合上班穿的衬衫，口碑好点，不要太贵"，前端回复"抱歉，暂时没有找到符合条件的商品，建议放宽筛选条件"。mock_data 中有 3 件衬衫（prod_002 H&M 商务免烫长袖衬衫、prod_003 ONLY 日系休闲亚麻短袖衬衫、prod_021 H&M 法式碎花雪纺衬衫），但全部未被召回。

**根因**: 三层问题叠加。

1. **Entity Extractor 输出粗粒度品类** — prompt 硬编码 8 个大品类（护肤/奶茶/数码/服饰/食品/家居/母婴/运动），"衬衫" 被映射为 `"category": "服饰"`，丢失了具体商品词信息
2. **Filter Builder 品类展开失败** — `_expand_category("服饰")` 做前缀匹配，但商品库品类是 `"男装/上装/T恤衬衫"` 格式，没有以 "服饰" 开头的品类 → 兜底返回 `["服饰"]` → Qdrant `MatchValue(value="服饰")` 匹配 0 条
3. **constraint_relaxation 链不含 category** — 放宽顺序是 brand → price → scenario → preference，category 不在其中，即使全部放宽完也救不了品类过滤错误

**修复方案**: 三层修复。

### 1. filter_builder.py — 品类标准化

- 新增 `_BROAD_CATEGORY_MAP`：大品类 → 前缀映射（"服饰" → ["男装/", "女装/"]）
- 新增 `_PRODUCT_TYPE_MAP`：商品词 → 实际品类映射（"衬衫" → ["男装/上装/T恤衬衫", "女装/上装/衬衫外套"]）
- `_expand_category(category, product_type)` 改为双参数，优先使用 product_type 精确映射
- `build_filter` 读取 entities 中的 `product_type` 字段传入展开函数

### 2. entity_extractor.py — 增加 product_type 字段

- SYSTEM_PROMPT 新增 `"product_type": "具体商品词或null"` 字段定义
- 引导 LLM 提取用户提到的具体商品词（衬衫/T恤/外套/面膜等）
- 输出 normalization 和 fallback 均包含 product_type

### 3. agent_tools.py — 放宽链增加 category

- `_RELAXATION_STEPS` 末尾新增 `("product_type", "去掉商品类型限制")` 和 `("category", "去掉品类硬过滤")`
- 作为最后手段，当前面所有放宽都无效时才去掉品类过滤

**修复后预期流程**:

```
用户: "我想买一件适合上班穿的衬衫，口碑好点，不要太贵"
↓
Entity Extractor:
  category=服饰, product_type=衬衫, scenario=上班, preference=口碑好
↓
Filter Builder:
  _expand_category("服饰", "衬衫") → ["男装/上装/T恤衬衫", "女装/上装/衬衫外套"]
  Qdrant Filter: MatchAny(["男装/上装/T恤衬衫", "女装/上装/衬衫外套"])
↓
Hybrid Search: 召回衬衫品类商品
↓
Ranker: 排序（product_type 匹配 + 上班场景 + 口碑偏好）
↓
返回衬衫商品推荐
```

**状态**: 已修复

---

## P10: tool_executor 日志缺少关键参数，无法判断 Agent 决策链路（已修复）

**发现时间**: 2026-05-19

**现象**: 日志只有 `tool_executed tool=product_search result_type=dict`，无法看到 Agent 传了什么 entities、semantic_query，也不知道返回了哪些 product_id。排查"Agent 是主动挑了 Polo 还是工具结果顺序导致"时无从下手。

**根因**: `tool_executor.py` 的 logger 只记录了 tool name 和 result type，没有记录 args 和 result 详情。

**解决方案**: tool_executor 新增结构化日志，按 tool 类型提取关键字段。

### 修改: src/graph/tool_executor.py

1. `_TOOL_ARGS_LOG_FIELDS` — 定义每个 tool 需要记录的 args 字段：
   - product_search: semantic_query + entities（仅 category/product_type/brand/scenario/preference/price，不含 user_id）
   - product_detail_batch / price_compare / review_summary: product_ids
   - constraint_relaxation: failed_reason
   - ask_clarification: asked_fields

2. `_TOOL_RESULT_LOG_FIELDS` — 定义每个 tool 需要记录的 result 字段：
   - product_search: total + 前 5 个 product_ids
   - product_detail_batch / review_summary: count + 前 5 个 product_ids
   - constraint_relaxation: relaxed + steps_remaining
   - ask_clarification: should_ask + question_count

3. `_extract_args_for_log()` — 提取 args 并过滤 PII
4. `_extract_result_for_log()` — 提取 result 关键字段

**日志输出示例**:
```
tool_executed tool=product_search
  args={semantic_query: "适合上班穿的衬衫", entities: {category: "服饰", product_type: "衬衫"}}
  result={total: 3, product_ids: ["prod_003", "prod_021", "prod_002"]}
```

**状态**: 已修复 — 非衬衫商品排在衬衫前面（已修复）

**发现时间**: 2026-05-19

**现象**: 用户搜索"衬衫"，品类展开正确召回了 24 件商品，但排序结果中 T 恤、背心、Polo 衫排在衬衫前面：

```
rank 1: 优衣库 运动速干跑步背心
rank 2: H&M 简约纯色V领T恤
rank 3: 森马 美式复古印花圆领T恤
rank 5: ONLY 日系休闲亚麻短袖衬衫  ← 真正的衬衫排到第 5
rank 8: H&M 商务免烫长袖衬衫      ← 真正的衬衫排到第 8
```

前端最终推荐了"ONLY 经典翻领Polo衫"，不符合用户"衬衫"需求。

**根因**: ranker 没有 product_type 匹配维度。

1. **品类太粗** — "男装/上装/T恤衬衫" 品类内混有 T 恤、背心、Polo、卫衣、羽绒服等 12 种商品，只有 3 件是衬衫
2. **ranker 不感知 product_type** — 排序只看 relevance/price/reputation/timeliness/personalization，没有"是否匹配用户要的商品类型"维度
3. **非衬衫靠价格/相关度胜出** — T 恤价格低（¥82-91），相关度也不差（同品类语义相似），综合分高于衬衫

**解决方案**: ranker 新增 product_type 匹配惩罚。

### 修改: src/agents/ranker.py

1. 新增 `_score_product_type_match(product, product_type)` 函数：
   - 检查商品**名称 + 特征**中是否包含 product_type 关键词
   - **不检查品类名**（品类 "T恤衬衫" 包含 "衬衫" 但不代表该商品是衬衫）
   - 匹配 → 1.0，不匹配 → 0.1（强惩罚，score × 0.1）

2. `rank()` 函数中：在 weighted fusion 之后，将 product_type_match 作为乘数应用于 composite score

**修复后排序效果**:

```
#1 ✓ ONLY 日系休闲亚麻短袖衬衫   ¥133  score=0.77  pt_match=1.0
#2 ✓ H&M 法式碎花雪纺衬衫        ¥160  score=0.76  pt_match=1.0
#3 ✓ H&M 商务免烫长袖衬衫        ¥185  score=0.73  pt_match=1.0
#4 ✗ 优衣库 运动速干跑步背心       ¥95  score=0.08  pt_match=0.1
#5 ✗ H&M 简约纯色V领T恤           ¥91  score=0.08  pt_match=0.1
```

衬衫 Top 3，非衬衫被 0.1x 惩罚压到底部。

**状态**: 已修复

**发现时间**: 2026-05-19

**现象**: 测试"我想买一件适合上班穿的衬衫"时，日志出现 3 次 ranker 排序调用。

**根因**: 两层重试机制叠加。

1. **product_search 内部重试**（P8 修复时引入）— 0 结果时自动去掉 category filter 重试一次，每次调用 rank() → 2 次
2. **Agent ReAct 循环**（原有）— Agent 不知道内部重试已找到结果，仍调用 constraint_relaxation + product_search → 再 1 次

总 rank() 调用 = 3 次。

**解决方案**: 去掉 product_search 内部重试机制，让 Agent 统一控制决策流。product_search 只负责单次检索+排序，重试逻辑完全由 ReAct 循环通过 constraint_relaxation 管理。

修正后流程（rank() 只调用 1 次）：
```
ReAct 轮次 1: product_search → filter_builder 展开品类 → hybrid_search → rank() ①
ReAct 轮次 2: Agent 看到结果 → 直接生成 Final Answer
```

**状态**: 已修复

---

## P11: mock_data 品类体系混乱 + 缺少 product_type 字段（已修复）

**发现时间**: 2026-05-19

**现象**: 多个排序和过滤问题的根源指向数据质量：
1. 品类字段混合了"类型"和"子品类"（如 "男装/上装/T恤衬衫"），导致品类展开和匹配逻辑复杂且易出错
2. 缺少 `product_type` 结构化字段，ranker 无法精确区分衬衫/T恤/Polo 衫
3. `embedding_text` 使用旧品类名（"T恤衬衫"），污染向量语义

**根因**: mock_data 设计时没有区分"品类"（粗粒度分类）和"商品类型"（具体商品词），将两者混在同一个 category 字段中。

**解决方案**: 数据重新设计 + 重建索引。

### 1. scripts/generate_clothing_data.py — 新数据生成脚本

- category 简化为 4 个干净品类：`男装/上装`、`男装/下装`、`女装/上装`、`女装/下装`
- 新增 `product_type` 字段：衬衫、T恤、Polo 衫、背心、卫衣、夹克、牛仔裤、西裤、休闲裤、短裤、半身裙、长裙等 23 种
- `embedding_text` 改为 `category + product_type + name + features`，不再使用旧混合品类
- 50 条商品，覆盖男装女装上下装

### 2. data/mock_data.json — 重新生成

旧数据 → 新数据对比：

| 字段 | 旧 | 新 |
|------|------|------|
| category | "男装/上装/T恤衬衫" | "男装/上装" |
| product_type | 无 | "衬衫" |
| embedding_text | "男装/上装/T恤衬衫 商务免烫..." | "男装/上装 衬衫 H&M 商务免烫长袖衬衫 棉 免烫..." |

### 3. scripts/build_index.py — payload 增加 product_type

Qdrant payload 新增 `product_type` 字段，支持结构化过滤。

### 4. 联动修改

- `filter_builder.py` — `_PRODUCT_TYPE_MAP` 更新为新品类格式
- `test_p8_shirt_search.py` — 测试断言更新为新品类

**状态**: 已修复

---

## P12: hybrid_retriever 日志缺少 product_type 过滤信息（已修复）

**发现时间**: 2026-05-19

**现象**: hybrid_retriever 的 `filter_built` 日志只显示 `"simple_filters": {"category": "服饰"}`，无法看到：
1. product_type 是否被提取和过滤
2. 品类展开后的实际匹配列表（normalized_categories）
3. product_type 过滤是否生效

**根因**: `_filter_to_dict()` 只提取 category 和 brand，不提取 product_type。`filter_built` 日志不记录展开后的品类列表。

**解决方案**: 两处修改。

### 修改: src/retrieval/hybrid_retriever.py

1. `_filter_to_dict()` — 新增 `product_type` 字段提取
2. `filter_built` 日志 — 新增三个字段：
   - `product_type`: 原始 product_type 值（如 "衬衫"）
   - `normalized_categories`: `_expand_category()` 展开后的品类列表（如 ["男装/上装", "女装/上装"]）
   - `product_type_filter_applied`: bool，是否有 product_type 参与过滤

**日志输出示例**:
```
filter_built
  simple_filters={category: "服饰", product_type: "衬衫"}
  has_complex_filter=true
  product_type="衬衫"
  normalized_categories=["男装/上装", "女装/上装"]
  product_type_filter_applied=true
```

**状态**: 已修复

---

## P13: product_detail_batch / review_summary 返回顺序与输入不一致（已修复）

**发现时间**: 2026-05-19

**现象**: Agent 调用 `product_detail_batch(["prod_005", "prod_004", "prod_006"])` 和 `review_summary(["prod_004", "prod_005", "prod_006"])`，两个工具返回的结果顺序都取决于数据文件中的存储顺序（prod_004, prod_005, prod_006），而非输入参数顺序。

**风险**: 如果后续生成文案时按列表下标合并 `details[i] + reviews[i]`，当两次调用传入的 product_ids 顺序不同时，会出现商品详情和评论摘要错配。

**根因**: `product_detail_batch`、`price_compare`、`review_summary` 三个工具都用 `[p for p in products if p["product_id"] in id_set]` 过滤，遍历顺序取决于 `load_products()` 返回顺序（数据文件顺序），不保留输入顺序。

**解决方案**: 三个工具统一改为先构建 `by_id` 字典，再按输入 `product_ids` 顺序提取：

```python
by_id = {p["product_id"]: p for p in products if p.get("product_id") in id_set}
matched = [by_id[pid] for pid in product_ids if pid in by_id]
```

### 修改文件

- `src/tools/product_detail.py` — `product_detail_batch()` 和 `price_compare()`
- `src/tools/review_tool.py` — `review_summary()`

**状态**: 已修复

---

## P14: Entity Extractor 缺少 gender 字段，无法区分男装/女装（已修复）

**发现时间**: 2026-05-19

**现象**: 用户提问"有没有适合夏天穿、不容易皱的男士衬衫"，entity extractor 输出：

```json
{
  "category": "服饰",
  "product_type": "衬衫",
  "scenario": "夏天",
  "preference": "不容易皱"
}
```

缺少 `gender: "男"`。导致 `_expand_category("服饰", "衬衫")` 返回 `["男装/上装", "女装/上装"]`，同时召回男装和女装衬衫。

**根因**: entity_extractor SYSTEM_PROMPT 没有 `gender` 字段定义，LLM 不会提取"男士/女士/男款/女款"等性别信息。

**解决方案**: 三层修改。

### 1. src/agents/entity_extractor.py — 新增 gender 字段

- SYSTEM_PROMPT 新增 `"gender": "男/女或null"` 字段定义
- 引导 LLM 提取"男士"→"男"、"女生"→"女"、"男款"→"男"等
- normalize 和 fallback 均包含 gender

### 2. src/retrieval/filter_builder.py — _expand_category 增加 gender 参数

- `_expand_category(category, product_type, gender)` 改为三参数
- 当 `gender="男"` 时，过滤结果只保留 `"男装/..."` 前缀的品类
- 当 `gender="女"` 时，过滤结果只保留 `"女装/..."` 前缀的品类
- `build_filter()` 从 entities 读取 gender 传入

### 3. src/retrieval/hybrid_retriever.py — 日志增加 gender

- `filter_built` 日志新增 `gender` 字段
- `_expand_category` 调用传入 gender

**修复后效果**:

```
用户: "有没有适合夏天穿、不容易皱的男士衬衫"
→ entities: {category: "服饰", product_type: "衬衫", gender: "男", ...}
→ _expand_category("服饰", "衬衫", "男") → ["男装/上装"]
→ 只召回男装衬衫，不再混入女装
```

**状态**: 已修复

---

## P15: Ranker 维度写死 + Agent 边界模糊 — 动态权重架构重构

**发现时间**: 2026-05-19

**现象**: 当前 ranker 存在两个结构性问题：
1. 每个新偏好维度（不容易皱、夏天穿、透气）都需要在 `_adjust_weights` 中写 if/else 分支，不可扩展
2. Agent 不应该管排序权重细节，只应决定工具路径

**根因**: `_adjust_weights` 采用硬编码关键词匹配模式，每新增一种用户偏好就要加一段 if/else。ranker 维度固定为 5 个，缺少通用的"属性匹配"维度。

**解决方案**: 三层架构重构。

### 1. Entity Extractor — 结构化需求解析

- SYSTEM_PROMPT 新增 `hard_constraints`（硬过滤条件）和 `soft_requirements`（软需求列表）
- soft_requirements 每个元素：`{"text": "原始描述", "type": "类型", "importance": 0.5-1.0}`
- type 示例：season_scene、functional_preference、style_preference、quality_signal、gift_context
- importance 引导：必须→1.0，最好→0.7，顺便→0.5
- 保留旧字段（scenario/preference）向后兼容

### 2. Ranker — 通用 match 函数 + rank profiles

**新增函数**:
- `_build_product_text(product)`: 拼接 name + product_type + features
- `_keyword_match_score(req_text, product_text)`: 关键词命中率 + 同义词扩展
- `_score_attribute_match(product, soft_requirements)`: 对每个 requirement 加权平均匹配分

**新增维度**: `attribute_match`（第 6 维），替代旧的 `_adjust_weights` 硬编码逻辑

**同义词配置**: `configs/ranking/synonyms.yaml`，约 8 组常见同义词（夏天→透气/速干/薄款，不容易皱→免烫/抗皱等）

**RANK_PROFILES**（4 个稳定 profile）:
- `default`: 均衡权重
- `price_sensitive`: 价格权重 0.30
- `quality_sensitive`: 口碑权重 0.25
- `scenario_preference`: 属性匹配权重 0.30

**select_rank_profile(entities)**: 根据 preference 关键词和 soft_requirements 数量自动选择 profile

**删除**: `_adjust_weights()` 函数（被 profile 选择器替代）

### 3. constraint_relaxation — 适配 soft_requirements

- `_RELAXATION_STEPS` 新增 `("soft_requirements", "去掉软需求限制")`
- 优先级在 price 之后、scenario 之前
- 列表字段清空为 `[]`，而非 `None`

### 4. react_prompt + tool_executor

- `_format_entities` 对 soft_requirements 特殊格式化为 `"软需求=[夏天穿(0.8), 不容易皱(0.9)]"`
- tool_executor 实体日志白名单新增 soft_requirements、hard_constraints

**修复后效果**:

```
用户: "有没有适合夏天穿、不容易皱的男士衬衫"
→ entity_extractor 输出:
  hard_constraints: {product_type: "衬衫", gender: "男"}
  soft_requirements: [
    {text: "夏天穿", type: "season_scene", importance: 0.8},
    {text: "不容易皱", type: "functional_preference", importance: 0.9}
  ]
→ ranker:
  select_rank_profile → "scenario_preference"（2 个软需求）
  attribute_match: 免烫衬衫=0.9, 普通T恤=0.1
  → 免烫衬衫排名高于普通T恤
```

**状态**: 已修复

---

## P17: attribute_match 评分失效 + evidence 缺失 + gender 未透传

**发现时间**: 2026-05-19

**现象**: attribute_match 维度所有商品得分都是 0.5（baseline），软需求没有实际区分商品；rank 日志缺少 attribute_evidence 无法排查；simple_filters 没有 gender 字段。

**根因**: 三个独立问题：

1. **同义词部分匹配失败** — `_keyword_match_score("夏天穿", product_text)` 调用 `synonyms.get("夏天穿")` 返回空列表（key 是 "夏天"），直接命中和精确同义词查找都失败，返回 0.0
2. **attribute_evidence 未返回** — `_score_attribute_match` 只返回 float 分数，不返回匹配证据；`rank()` 也没有把 evidence 附加到结果中
3. **simple_filters 缺少 gender** — `_filter_to_dict` 只处理 category/brand/price，不处理 gender，导致无复杂 filter 时 gender 过滤丢失

**解决方案**:

### 1. 同义词部分匹配（ranker.py）

`_keyword_match_score` 新增第 3 级匹配：遍历 synonym keys，检查 key 是否是 keyword 的子串。

```python
# 3. Partial match: kw contains a synonym key (e.g. "夏天穿" contains "夏天")
for key, syn_list in synonyms.items():
    if key in kw_lower:
        matched_syn = _find_in_product([key] + syn_list, product_lower)
        if matched_syn:
            hits += 1
            matched_terms.append(matched_syn)
            break
```

返回类型从 `float` 改为 `tuple[float, list[str]]`，携带匹配证据。

### 2. attribute_evidence 透传（ranker.py）

- `_score_attribute_match` 返回 `(score, evidence)` 元组
- `rank()` 解包后将 `attribute_evidence` 附加到排序结果
- `rank_detail` 日志新增 `attribute_evidence` 字段

### 3. gender 透传到 simple_filters（hybrid_retriever.py）

`_filter_to_dict` 新增 gender 字段：

```python
if entities.get("gender"):
    filters["gender"] = entities["gender"]
```

**验证**: 36/36 测试通过（test_p8_shirt_search.py 18 个 + test_t24.py 18 个），含新增的 `test_keyword_match_score_partial` 测试。

**状态**: 已修复

---

## P18: intent_classifier 输出空 intent + product_search 缺少 exact/supplemental 区分

**发现时间**: 2026-05-19

**现象**: 预处理输出 `"intent": "", "confidence": 0.0`，应为 `"search"`。虽然后续 Agent 仍正常调用 product_search，不影响主流程，但属于预处理异常。

**根因**: 两个独立问题：

1. **classify_intent 无兜底** — semantic_router 返回空 → llm_router 异常/返回空 → 直接返回 `("", 0.0)`，没有最终默认值
2. **config key 读取错误** — `intent_classifier._get_threshold()` 读 `config["semantic_router"]["threshold"]`（不存在），实际配置是 `router.semantic_threshold`

**解决方案**:

### 1. classify_intent 兜底默认值（intent_classifier.py）

两层都返回空时，默认返回 `"search"` + 0.0 confidence + source="default"：

```python
if not intent:
    intent, confidence = "search", 0.0
    source = "default"
```

### 2. config key 修正（intent_classifier.py）

```python
# 修正前
return config.get("semantic_router", {}).get("threshold", 0.80)
# 修正后
return config.get("router", {}).get("semantic_threshold", 0.80)
```

### 3. product_search 区分 exact/supplemental（product_search.py）

新增 `_split_matches(ranked, product_type)`：
- `exact_matches`: product_type_match >= 0.9（名称或类型字段直接匹配）
- `supplemental_matches`: product_type_match < 0.9（属性匹配但类型不符）

返回结果新增 `exact_matches` 和 `supplemental_matches` 列表。

**状态**: 已修复

---

## P19: product_search 结果结构优化 + attribute_scores 细化

**发现时间**: 2026-05-19

**现象**:
1. product_search 返回的 `product_ids` 混入了 supplemental 商品，Agent 无法区分主推荐和补充商品
2. `attribute_evidence` 只记录命中项，未命中项缺失，无法生成完整解释

**解决方案**:

### 1. product_search 返回 display_product_ids（product_search.py）

返回结果新增分组字段：

```json
{
  "exact": 3,
  "supplemental": 7,
  "exact_product_ids": ["prod_005", "prod_006", "prod_004"],
  "supplemental_product_ids": ["prod_007", "prod_009"],
  "display_product_ids": ["prod_005", "prod_006", "prod_004"]
}
```

Agent 主推荐只用 `display_product_ids`（= exact_product_ids）。

### 2. tool_executor 日志适配（tool_executor.py）

product_search 日志输出 exact/supplemental 数量和分组 ID。

### 3. attribute_scores 细化为分项分数（ranker.py）

`_score_attribute_match` 返回值从 `{req_text: [matched_terms]}` 改为：

```json
{
  "夏天穿": {"score": 1.0, "matched_terms": ["透气"]},
  "不容易皱": {"score": 0.0, "matched_terms": []}
}
```

每个软需求都包含在 attribute_scores 中（含未命中项），便于后续生成解释。

**状态**: 已修复

---

## P20: 服饰场景歧义未识别 + 澄清流程不完整

**发现时间**: 2026-05-19

**现象**: 用户提问"我下周要面试，想买一件看起来正式但不太老气的衣服"，系统直接按单次检索处理，product_type="衣服" 无法精确匹配，exact=0, supplemental=10。追问后用户回复"男性的衬衫"被当成新的独立搜索，丢失了面试、正式、不老气等上下文。

**根因**: 三层问题叠加。

1. **entity_validator 缺失** — entity_extractor 只处理语义歧义（"苹果"是水果还是手机），不处理关键字段缺失。gender=null、product_type="衣服" 时 ambiguous=false
2. **search_planner 时机错误** — 即使 ambiguous=true，search_planner 仍会执行并生成 outfit_multi_query，流程不干净
3. **澄清回答被当成新请求** — 用户回复"男性的衬衫"时，系统重新走 intent_classifier → entity_extractor，丢失了上一轮的面试、正式、不老气等上下文

**解决方案**: 三层修复。

### 1. entity_validator — 后置规则校验

新增 `src/agents/entity_validator.py`：
- 检查品类+缺失字段的组合（服饰+gender缺失、服饰+product_type模糊、护肤+skin_type缺失）
- 标记 `missing_critical_fields` 列表
- 设置 `ambiguous=true`

### 2. ask_clarification — 策略化追问

改造 `src/tools/agent_tools.py` 的 `ask_clarification`：
- 语义歧义 → 必须追问
- 服饰类 gender+product_type 都缺 → 优先追问
- 只缺 gender，product_type 明确 → 轻量追问
- 已追问过一次 → 不再追问，按假设继续
- 返回结构化结果：`{should_ask, strategy, question, assumptions, response_note}`

### 3. clarification_parser — 澄清回答合并

新增 `src/agents/clarification_parser.py`：
- 解析用户回复中的 gender（男/女）、product_type（衬衫/西裤等）、style（正式/休闲等）
- 合并到上一轮的 entities 中
- 清除已解决的 missing_critical_fields

### 4. 状态跟踪 — pending_clarification

- `state.py` 新增 `pending_clarification` 字段
- `react_node.py`：当 agent 返回澄清问题时，设置 `pending_clarification`
- `preprocessing.py`：检测到 `pending_clarification` 时，走澄清回答路径而非正常提取

### 5. search_planner 时机修正

- `preprocessing.py`：当 `missing_critical_fields` 非空时，跳过 search_planner，返回 `search_mode="deferred"`

**修复后流程**:

```
用户: "我下周要面试，想买一件看起来正式但不太老气的衣服"
↓
entity_validator: missing=[gender, product_type], ambiguous=true
↓
search_planner: 跳过（search_mode=deferred）
↓
Agent: 调用 ask_clarification → should_ask=true, strategy=ask
↓
Agent: 返回追问 "你想看男装还是女装？面试的话，我建议优先看衬衫或西装外套。"
↓
state: pending_clarification = {fields: [gender, product_type], entities_snapshot: {...}}
↓
用户: "男性的衬衫"
↓
preprocessing: 检测到 pending_clarification → 走 clarification_parser
↓
clarification_parser: gender=男, product_type=衬衫, 合并到之前的 entities
↓
merged entities: {gender: "男", product_type: "衬衫", scenario: "面试", soft_requirements: [正式, 不老气]}
↓
search_planner: 生成 single 检索计划
↓
Agent: 调用 product_search → 返回衬衫推荐
```

**状态**: 已修复

---

## P21: Intent 分类粒度不足 — "search" 和 "recommend" 无法区分执行路径

**发现时间**: 2026-05-19

**现象**: "帮我找衬衫" 和 "推荐几款适合面试的衬衫" 都分类为 `intent="search"`，下游 Agent 无法根据意图选择不同的检索策略（直接搜索 vs 场景推荐）。"帮我搭一套通勤穿搭" 也被分为 "search"，丢失了 "需要多品类组合" 的执行信息。

**根因**: intent 只有一层分类（search/recommend/compare/detail/order），粒度太粗：
1. "search" 和 "recommend" 混为一谈 — 用户找商品和请求推荐是不同的交互模式
2. 缺少任务类型维度 — 简单搜索、场景推荐、穿搭规划、比价是不同复杂度的任务
3. 缺少执行建议 — Agent 需要知道是直接搜索、多 query 检索还是先澄清

**解决方案**: 三层 intent 结构。

### 1. 三层 intent 定义

| 层级 | 字段 | 取值 | 含义 |
|------|------|------|------|
| 用户目标 | user_goal | recommend_product / find_product / compare_products / view_detail / place_order | 用户想做什么 |
| 任务类型 | task_type | shopping_advice / product_search / outfit_planning / price_comparison / detail_inquiry / order_placement | 任务复杂度 |
| 执行建议 | execution_hint | contextual_search / direct_search / multi_query / compare / get_detail / clarify_first | Agent 怎么执行 |

### 2. 修改文件

- `src/router/llm_router.py` — SYSTEM_PROMPT 重写为三层输出，新增判断规则和示例
- `src/router/semantic_router.py` — `_INTENT_MAP` 将旧 flat intent 映射到三层结构
- `src/router/intent_classifier.py` — 返回 `(dict, float, str)`，兜底默认值改为 dict
- `src/graph/state.py` — `intent: str` → `intent: dict`
- `src/graph/preprocessing.py` — Path B 默认值从 `"search"` 改为 `{}`
- `src/agents/react_prompt.py` — 新增 `_format_intent()` 函数，格式化为 `user_goal / task_type / execution_hint`
- `src/graph/postprocessing.py` — 从 intent dict 提取 `user_goal` 字符串传给 `write_chunk`

### 3. 向后兼容

- 旧架构 `shopping_graph.py` 不修改，仍使用 flat intent 字符串
- 新旧架构通过 API 切换，互不影响

**示例**:

```
"帮我找衬衫"
→ {user_goal: find_product, task_type: product_search, execution_hint: direct_search}

"推荐几款适合面试的衬衫"
→ {user_goal: recommend_product, task_type: shopping_advice, execution_hint: contextual_search}

"帮我搭一套通勤穿搭"
→ {user_goal: recommend_product, task_type: outfit_planning, execution_hint: multi_query}

"我下周要面试，想买衣服"
→ {user_goal: recommend_product, task_type: shopping_advice, execution_hint: clarify_first}
```

**状态**: 已修复

---

## P22: ask_clarification 返回预构建 NL 文本，系统丢失追问结构信息

**发现时间**: 2026-05-19

**现象**: `ask_clarification` tool 返回 `should_ask=true`，但 `question_count=0`，没有返回 `fields`、`question_type`、`question_spec` 等结构化信息。系统只知道"要追问"，不知道"追问了什么"。

**影响**:
1. `pending_clarification` 无法记录追问的具体字段（gender? product_type? skin_type?）
2. 下一轮用户回答时，`clarification_parser` 不知道该解析哪些字段
3. 无法区分"追问了 gender+product_type"和"追问了预算"这两种不同场景
4. 状态机缺少 `question_count`，无法正确管理追问轮次

**根因**: `ask_clarification` 的设计定位是"生成追问文本"，而不是"决策追问结构"。每个 return path 都用 `_build_clothing_question()` 等函数预构建自然语言字符串（`question` 字段），但没有返回结构化的追问意图。

**解决方案**: Tool 决策 + LLM 表达的职责分离。

### 1. agent_tools.py — 返回结构化 question_spec

每个 return path 的返回结构从：

```python
{
    "should_ask": True,
    "strategy": "ask",
    "priority_fields": ["gender", "product_type"],
    "question": "你想看男装还是女装？面试的话，我建议优先看衬衫或西装外套。",  # 预构建 NL
    "assumptions": None,
    "response_note": None,
}
```

改为：

```python
{
    "should_ask": True,
    "strategy": "ask",
    "fields": ["gender", "product_type"],       # 追问哪些字段
    "question_count": 1,                         # 本次追问几个问题
    "question_type": "clothing_gender_and_type", # 语义类型
    "question_spec": {                           # 结构化追问规格
        "must_ask": ["gender", "product_type"],
        "context": "面试场景",
        "suggestions": {"product_type": ["衬衫", "西装外套"]}
    },
    "assumptions": None,
}
```

删除 `_build_semantic_question()`、`_build_clothing_question()`、`_build_assume_note()` 三个 NL 构建函数。

### 2. react_node.py — pending_clarification 使用新字段

```python
result["pending_clarification"] = {
    "fields": data.get("fields", []),
    "question_spec": data.get("question_spec", {}),
    "question_type": data.get("question_type", ""),
    "strategy": data.get("strategy", ""),
    "entities_snapshot": entities,
}
```

### 3. react_prompt.py — 教 LLM 如何使用 question_spec

决策规则新增 `question_spec` 使用说明：
- `question_spec.must_ask`: 必须问的字段
- `question_spec.context`: 场景上下文，融入追问文本
- `question_spec.suggestions`: 建议选项，引导用户选择
- 追问文本要自然流畅，不要机械罗列字段名

### 4. state.py — pending_clarification 注释更新

```python
pending_clarification: dict | None  # {fields, question_spec, question_type, strategy, entities_snapshot}
```

**修复后效果**:

```
ask_clarification 返回:
{
    "should_ask": True,
    "strategy": "ask",
    "fields": ["gender", "product_type"],
    "question_count": 1,
    "question_type": "clothing_gender_and_type",
    "question_spec": {
        "must_ask": ["gender", "product_type"],
        "context": "面试场景",
        "suggestions": {"product_type": ["衬衫", "西装外套"]}
    }
}

LLM 生成: "你想看男装还是女装？面试的话，我建议优先看衬衫或西装外套。"

系统侧始终知道:
- 问了几个问题: question_count=1
- 问的是什么字段: fields=[gender, product_type]
- 用户回答时该解析什么: pending_clarification.fields
```

**状态**: 已修复

---

## P23: pending_clarification 跨轮次丢失 — AgentState 缺少字段定义

**发现时间**: 2026-05-19

**现象**: 第一轮 `ask_clarification` 正确设置了 `pending_clarification`（日志可见 `pending_clarification_set fields=["gender", "product_type"]`），但第二轮用户回复"男性的衬衫"后，系统重新走了完整的正常流程（entity_extractor → intent_classifier → entity_validator → search_planner），而非澄清回答路径。

第二轮 intent 被重新分类为：
```
user_goal = find_product
task_type = product_search
execution_hint = direct_search
```

说明 `preprocessing.py` 的 Path B（`if pending:` 分支）完全没有触发。

**根因**: `AgentState` TypedDict 缺少 `pending_clarification` 和 `search_plan` 字段定义。

LangGraph 的 checkpointer 只持久化 TypedDict 中定义的字段。`react_node.py` 返回 `{"pending_clarification": {...}}` 时，LangGraph 将其合并到内存中的 state dict，但 checkpointer 序列化时会丢弃未在 TypedDict 中声明的字段。第二轮加载 checkpoint 时，`pending_clarification` 不存在，Path B 不触发。

同理，`search_plan` 也未在 `AgentState` 中声明，`react_node.py` 读取 `state.get("search_plan", {})` 始终返回空 dict。

**对比**:

| 文件 | 定义的 State | 有 pending_clarification? |
|------|------|------|
| `state.py` (ShoppingState) | 旧工作流 | ✓ |
| `agent_state.py` (AgentState) | 新 Agent 图 | ✗ ← 问题 |

`shopping_agent.py` 使用 `AgentState`，不是 `ShoppingState`。

**解决方案**: 补全 `AgentState` 字段定义。

### 1. agent_state.py — 新增两个字段

```python
class AgentState(TypedDict):
    # ... existing fields ...

    # === Deterministic preprocessing output ===
    search_plan: dict                           # Search plan from search_planner

    # === Clarification state ===
    pending_clarification: dict | None          # Awaiting clarification answer
```

同时修正 `intent` 类型：`str` → `dict`（与 P21 三层 intent 结构一致）。

### 2. shopping_agent.py — initial_state 类型修正

```python
initial_state = {
    ...
    "intent": {},        # was "" (str), now dict
    "search_plan": {},   # new field
    ...
}
```

注意：`pending_clarification` **不加入** initial_state。这样第二轮加载 checkpoint 时，该字段从上一轮的 checkpoint 中恢复，而非被初始值覆盖。

**修复后跨轮次流程**:

```
第一轮:
  preprocess → react_loop → ask_clarification(should_ask=true)
  → state.pending_clarification = {fields: [gender, product_type], ...}
  → checkpointer 保存（AgentState 声明了该字段，不会被丢弃）

第二轮:
  run_agent_stream 创建 initial_state（不含 pending_clarification）
  → LangGraph 加载 checkpoint，合并 initial_state
  → pending_clarification 从 checkpoint 恢复（不在 initial_state 中，不被覆盖）
  → preprocess 检测到 pending → 走 Path B
  → clarification_parser 解析 "男性的衬衫" → gender=男, product_type=衬衫
  → 合并上一轮 entities（保留 scenario=面试, soft_requirements=[正式, 不老气]）
  → 清除 pending_clarification
  → plan_search → Agent 检索
```

**状态**: 已修复

---

## P24: 上下文合并日志缺失 + pending_clarification 清除无日志

**发现时间**: 2026-05-20

**现象**: 第二轮 preprocessing 日志显示 `soft_req_count=0, scenario=null`，但 product_search 实际收到的 entities 包含 `scenario="面试", soft_requirements=["正式", "不老气", "面试"]`。上下文确实被补回来了，但日志没有告诉你是哪个模块、在哪一步合并的。

同时 `pending_clarification` 被清除时没有日志，无法确认清除是否发生、清除原因是什么。

**根因**: 两个日志缺失。

1. **合并过程不可见** — `clarification_merged` 日志只记录 `gender`、`product_type`、`remaining_missing`，不记录从上一轮快照继承了哪些字段（scenario、soft_requirements 等），也不输出完整合并结果
2. **清除无日志** — `pending_clarification: None` 在 return 时直接设置，没有日志记录清除动作和原因

**解决方案**: 补全 Path B 日志链路。

### 1. preprocessing.py — 新增 context_merge_done 日志

在 `parse_clarification_answer` 返回后，记录完整的合并过程：

```python
logger.info("context_merge_done",
    source="pending_clarification.entities_snapshot",
    current_entities={k: entities.get(k) for k in pending_fields},  # 本轮解析的
    merged_entities={...},    # 完整合并结果（非空字段）
    carried_fields=[...],     # 从快照继承的字段（非 pending_fields）
    gender=..., product_type=..., scenario=...,
    soft_req_count=...,
    remaining_missing=...)
```

### 2. preprocessing.py — 新增 pending_clarification_cleared 日志

在 return 之前记录清除动作：

```python
filled_fields = [f for f in pending_fields if entities.get(f) is not None]
reason = "all_required_fields_filled" if not missing_fields else "partial_fields_filled"
logger.info("pending_clarification_cleared",
    reason=reason,
    filled_fields=filled_fields,
    remaining_missing=missing_fields)
```

**修复后 Path B 完整日志链路**:

```
clarification_answer_detected
  pending_fields=[gender, product_type]
  user_input="男性的衬衫"

context_merge_done
  source=pending_clarification.entities_snapshot
  current_entities={gender: 男, product_type: 衬衫}
  carried_fields=[category, quantity, scenario, soft_requirements]
  merged_entities={category: 服饰, gender: 男, product_type: 衬衫, scenario: 面试, ...}
  soft_req_count=3

pending_clarification_cleared
  reason=all_required_fields_filled
  filled_fields=[gender, product_type]
  remaining_missing=[]
```

**状态**: 已修复

---

## P25: tool_executor 日志 question_count 始终为 0 — log extractor 读取了不存在的字段

**发现时间**: 2026-05-20

**现象**: `ask_clarification` tool 返回 `should_ask=true, question_count=1`，但 `tool_executor` 日志显示 `question_count=0`。日志中 `pending_clarification_set` 能正确显示 `question_type` 和 `fields`（因为 react_node 直接读 tool 返回），但 `tool_executed` 日志始终显示 `question_count=0`。

**根因**: `tool_executor.py` 的 `ask_clarification` log extractor 读取了不存在的字段：

```python
# 错误：tool 返回中没有 "questions" 列表
"question_count": len(r.get("questions", [])),  # 永远返回 0
```

`ask_clarification` 返回的是 `question_count: int`（直接数字），不是 `questions: list`。`r.get("questions", [])` 返回空列表，`len([])` = 0。

**解决方案**: 修改 log extractor，直接读取 tool 返回的实际字段：

```python
"ask_clarification": lambda r: {
    "should_ask": r.get("should_ask", False),
    "strategy": r.get("strategy", ""),
    "fields": r.get("fields", []),
    "question_count": r.get("question_count", 0),
    "question_type": r.get("question_type", ""),
},
```

**修复后日志输出**:

```
tool_executed tool=ask_clarification
  args={asked_fields: [...]}
  result={should_ask: true, strategy: "ask", fields: ["gender", "product_type"],
          question_count: 1, question_type: "clothing_gender_and_type"}
```

**状态**: 已修复

---

## P26: 澄清路径缺少任务切换检测 — 用户换任务时被错误合并到旧 entities

**发现时间**: 2026-05-20

**现象**: 第一轮追问 gender+product_type，第二轮用户说"算了，我想买鞋"，系统把"鞋"错误合并到上一轮"面试服饰推荐"的 entities 里，导致后续状态混乱。

**根因**: `preprocessing.py` Path B 检测到 `pending_clarification` 后，无条件调用 `parse_clarification_answer()`，没有判断用户的回复是澄清回答还是任务切换。

**风险场景**:

| 用户输入 | 实际意图 | 当前行为 | 正确行为 |
|------|------|------|------|
| 男性的衬衫 | 澄清回答 | 合并 ✓ | 合并 ✓ |
| 我想买男士衬衫 | 澄清回答 | 合并 ✓ | 合并 ✓ |
| 算了，我想买鞋 | 任务切换 | 错误合并 ✗ | 清除 pending，走正常流程 |
| 不用了，帮我看平板电脑 | 任务切换 | 错误合并 ✗ | 清除 pending，走正常流程 |
| 换成皮鞋吧 | 同场景换品类 | 错误合并 ✗ | 保留 scenario，换 product_type |
| 随便吧 | 不明确 | 错误合并 ✗ | 再追问或假设继续 |

**解决方案**: 新增 `src/agents/clarification_router.py`，在 Path B 入口做路由判定。

### 1. clarification_router.py — 规则优先 + LLM 兜底

**路由优先级**:

```
1. 强取消词 + 新品类 → task_switch_full
   取消词: 不用了/不要了/算了/取消/不找了
   且出现与 pending 无关的新品类

2. 明确包含 pending_fields 的值 → clarification_answer
   检测性别词（男/女/男士/女装...）和商品类型词

3. 弱切换词 + 新品类 → task_switch_partial
   切换词: 换成/改成/换个/我想买/帮我看
   且出现新商品品类

4. 以上都不满足 → LLM 兜底
```

**task_switch 分两种**:
- `task_switch_full`：完全换任务（"帮我看看平板电脑"）→ 清空旧 scenario、soft_requirements
- `task_switch_partial`：同场景换品类（"换成皮鞋吧"）→ 保留 scenario 和 soft_requirements

**方案 B**：router 顺便解析 parsed_fields，后面直接用，不再二次调用 parser。

### 2. preprocessing.py — Path B 重构

```
if pending:
    routing = await route_clarification(...)

    task_switch_full → 清除 pending → 走正常 Path A
    task_switch_partial → 清除 pending → 走 Path A + 注入旧 scenario/soft_requirements
    clarification_answer → 用 parsed_fields 合并 → 走 Path B 后续
    unclear → 清除 pending → deferred search_plan → Agent 决策
```

### 3. 新增文件

| 文件 | 用途 |
|------|------|
| `src/agents/clarification_router.py` | 路由判定 + 字段解析 |

### 4. 修改文件

| 文件 | 改动 |
|------|------|
| `src/graph/preprocessing.py` | Path B 入口调用 router，task_switch 回退 Path A |

**验证场景**:

| 输入 | route | 效果 |
|------|------|------|
| 男性的衬衫 | clarification_answer | gender=男, product_type=衬衫，合并旧 entities |
| 我想买男士衬衫 | clarification_answer | 同上（"我想买"不误判为切换） |
| 算了，我想买鞋 | task_switch_full | 清空旧场景，重新提取 |
| 不用了，帮我看平板电脑 | task_switch_full | 清空旧场景，重新提取 |
| 换成皮鞋吧 | task_switch_partial | 保留面试场景，换 product_type 为皮鞋 |
| 随便吧 | unclear | 清除 pending，Agent 按 assume 处理 |

**状态**: 已修复

## P20: prompt 模板 JSON 示例的 `{}` 被 `.format()` 误解析导致 KeyError

**发现时间**: 2026-05-19

**现象**: 用户提问"我想买一件适合上班穿的衬衫，口碑好点，不要太贵，男装"，报错：
```
KeyError: '\n  "recommendations"'
```
完整 traceback 指向 `react_prompt.py:177` → `_SYSTEM_TEMPLATE.format(...)`。

**根因**: `_SYSTEM_TEMPLATE` 中的 Final Answer JSON 示例包含 `{` 和 `}` 字符：
```python
_SYSTEM_TEMPLATE = """...
```json
{
  "recommendations": [
    {"product_id": "商品ID", "text": "..."},
  ],
  "summary": "..."
}
```
..."""
```
调用 `_SYSTEM_TEMPLATE.format(intent=..., entities=..., ...)` 时，Python 的 `str.format()` 把 JSON 示例中的 `{"product_id": ...}` 当作格式化占位符，尝试解析 key `product_id` → 实际触发的是 `recommendations` 外层的 `\n  "recommendations"` 作为 format key，抛出 KeyError。

**解决方案**: 模板中 JSON 示例的 `{` 和 `}` 全部转义为 `{{` 和 `}}`。

**额外修复**（非本次根因，但是防御性改进）:
1. `react_node.py:115` — key normalization 增强：`k.strip().strip('"').strip("'").strip()`
2. `llm_client.py` — `chat_json` 增加 regex fallback（提取 `{...}` 块），与 `react_node.py` 对齐

**状态**: 已修复

---

## P27: LLM API 连接失败无重试 + 前端显示原始报错

**发现时间**: 2026-05-20

**现象**: 用户提问"我想买个送朋友的礼物，预算 200 左右"后补充"送女生"，后端报错 `httpx.ConnectError: All connection attempts failed`（DeepSeek API 临时不可达），前端显示"错误：All connection attempts failed"，用户体验差。

**根因**: 两个问题叠加。

1. **LLM 客户端无重试机制** — `llm_client.py` 的 `chat()` 方法直接发起 HTTP 请求，连接失败时立即抛出异常，没有重试。网络波动或 API 短暂不可达时直接失败。
2. **错误信息未转换为用户友好文案** — `multi_agent_graph.py` 和 `shopping_agent.py` 的异常处理直接将 `str(e)` 传给前端，显示原始技术错误信息。

**解决方案**: 两层修复。

### 1. llm_client.py — 指数退避重试

`LLMClient.chat()` 新增重试机制：

- 最多重试 3 次（`_MAX_RETRIES = 3`）
- 指数退避：1s → 2s → 4s（`_BASE_DELAY = 1.0`）
- 可重试错误类型：`ConnectError`、`ReadTimeout`、`ConnectTimeout`、HTTP 5xx
- 不可重试：HTTP 4xx（客户端错误，重试无意义）
- 每次重试记录 `llm_retry` 日志（attempt、error_type、delay）

```python
for attempt in range(_MAX_RETRIES):
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
        last_error = e
        delay = _BASE_DELAY * (2 ** attempt)
        logger.warning("llm_retry", attempt=attempt + 1, ...)
        if attempt < _MAX_RETRIES - 1:
            await asyncio.sleep(delay)
```

### 2. multi_agent_graph.py / shopping_agent.py — 用户友好错误提示

新增 `_friendly_error()` 函数，将技术错误转换为友好文案：

| 错误类型 | 前端显示 |
|---------|---------|
| ConnectError / ReadTimeout / ConnectTimeout | "抱歉，系统暂时无法连接到AI服务，请稍后再试。" |
| HTTP 5xx | "抱歉，AI服务暂时不可用，请稍后再试。" |
| 其他异常 | "抱歉，处理过程中出现了问题，请稍后再试。" |

异常处理中 `yield {"event": "error", "data": {"error": user_msg, "severity": "low"}}` 替代原来的 `str(e)`。

### 3. useChatStream.ts — 前端适配

前端 `handleSSEEvent` 的 error 分支移除 `错误：` 前缀，直接显示后端返回的友好文案。

**修改文件**:

| 文件 | 改动 |
|------|------|
| `src/models/llm_client.py` | chat() 新增指数退避重试（3 次） |
| `src/graph/multi_agent_graph.py` | 异常处理使用 _friendly_error() |
| `src/graph/shopping_agent.py` | 异常处理使用 _friendly_error() |
| `frontend/src/hooks/useChatStream.ts` | error 事件移除"错误："前缀 |

**状态**: 已修复

---

## P28: Path A 无任务切换检测 — 跨任务价格约束被错误继承

**发现时间**: 2026-05-20

**现象**: 用户先问"我想买个送朋友的礼物，预算 200 左右"，系统提取 `scenario: "送礼"`, `price_max: 250`。然后用户说"我下周面试，想买正式但不老气的衣服"，系统从上一轮继承了 `price_max: 250`，导致面试正装检索结果不足。

**根因**: Path A（`_run_normal_preprocessing`）的上下文继承逻辑无条件继承所有缺失字段（包括 `price_min`, `price_max`, `scenario`），没有判断用户是否切换了任务。Path B（`clarification_router.py`）有任务切换检测，但 Path A 没有。

**解决方案**: 在 `_run_normal_preprocessing` 中新增 `_detect_task_switch()` 函数，在继承前判断是否发生了任务切换：

1. **检测信号**: scenario 变化（"送礼" → "面试"）、category 变化（"服饰" → "数码"）、product_type 变化（"双肩包" → "手机"，排除同类服饰内切换）
2. **任务切换时**: 只继承结构性字段（category, product_type, brand），跳过任务特定字段（scenario, price_min, price_max, soft_requirements）
3. **同一任务时**: 保持原有逻辑，继承所有缺失字段 + hard_constraints 合并

```python
def _detect_task_switch(current: dict, previous: dict) -> bool:
    # Scenario changed → task switch
    if prev_scenario and cur_scenario and prev_scenario != cur_scenario:
        return True
    # Category changed → task switch
    if prev_category and cur_category and prev_category != cur_category:
        return True
    # Product type changed (excluding same clothing family) → task switch
    if prev_pt and cur_pt and prev_pt != cur_pt:
        if prev_pt in _CLOTHING_TYPES and cur_pt in _CLOTHING_TYPES:
            return False
        return True
    return False
```

**修改文件**:

| 文件 | 改动 |
|------|------|
| `src/graph/preprocessing.py` | 新增 `_detect_task_switch()` + 继承逻辑分支（task_switch vs continuation） |

**状态**: 已修复

---

## P29: 澄清追问时前端仍显示上一轮商品卡片

**发现时间**: 2026-05-20

**现象**: 用户第一轮"送礼 预算200"得到推荐后，第二轮"面试 买衣服"触发澄清追问（"请问您是男生还是女生？"），但前端在追问下方仍然显示了上一轮的 10 个商品卡片。

**根因**: `run_multi_agent_stream` 中 `search_results` 的提取逻辑从 `state_values.get("search_results", [])` 或 `tool_calls_log` 获取。当 agent 只调用 `ask_clarification` 而未调用搜索工具时：
- `search_results` 未被本轮 agent 覆盖，checkpointer 保留了上一轮的值（10 个商品）
- `tool_calls_log` 是 reducer（`_add_lists`），跨轮累积，包含上一轮的 `multi_query_search` 记录
- `recommendations` 为空 `[]`（agent 没有生成推荐）
- 但 `if search_results:` 判断为 True → 发送了旧商品的 `results` 事件

**解决方案**: 在发送 `results` 事件前，同时检查 `search_results` 和 `recommendations`。只有两者都非空时才发送。没有推荐 = 澄清/纯文本回复，不需要商品卡片。

```python
# 修复前
if search_results:

# 修复后
if search_results and recommendations:
```

**修改文件**:

| 文件 | 改动 |
|------|------|
| `src/graph/multi_agent_graph.py` | `results` 事件发送条件增加 `and recommendations` |
| `src/graph/shopping_agent.py` | 同上 |

**状态**: 已修复

---

## P30: 多轮对话状态污染 — Task Switch 结构性继承 + ask_clarification 死锁 + 僵尸 product_ids

**发现时间**: 2026-05-21

**现象**: 三轮对话后系统崩溃：

1. 第一轮"新秀丽/预算700"→ 正常返回 prod_055
2. 第二轮"国家地理/大容量/排除小米"→ 继承了第一轮的 brand 和 price_max，但 Agent 自我纠偏成功
3. 第三轮"先不看包了，看300元送礼好物"→ 崩盘：
   - Preprocessing 把 category="箱包"、product_type="包"、brand="国家地理" 继承过来
   - Agent 搜索"国家地理 300元送礼"→ 0 结果
   - Agent 调 ask_clarification → 返回 should_ask=false（死锁）
   - Fallback 触发 → 文字说"抱歉没找到"，但前端推了前两轮的包卡片

**根因**: 三层问题叠加。

### 崩溃点 A: Task Switch 结构性继承未清空

`preprocessing.py` 的 `_detect_task_switch` 正确检测到了 scenario 变化（"通勤"→"生日送礼"），但在继承逻辑中，task_switch 分支仍然把 category/product_type/brand 作为"结构性字段"继承：

```python
# 旧代码
if task_switched:
    _STRUCTURAL_FIELDS = ("category", "product_type", "brand")  # ← 这些也被继承了
    for field in _STRUCTURAL_FIELDS:
        if not entities.get(field) and prev_entities.get(field):
            entities[field] = prev_entities[field]  # ← 灾难：品牌="国家地理"被带入送礼场景
```

导致第三轮的底层硬约束变成了：`brand="国家地理" + price_max=300 + category="箱包"`。

### 崩溃点 B: ask_clarification 死锁

Agent 搜索 0 结果后调用 `ask_clarification`，但函数内部逻辑：

1. `missing_critical_fields=[]`（没有缺失字段）→ 走到 "No missing fields" 分支
2. 返回 `should_ask=False, strategy="none"` → Agent 无路可走
3. Agent 再次搜索 → 0 结果 → 再调 ask_clarification → 还是 false
4. 触发 `agent_loop_detected` → fallback

`ask_clarification` 没有"搜索失败"的处理分支，当搜索结果为 0 且约束冲突时，无法引导用户调整方向。

### 崩溃点 C: 僵尸 product_ids 渲染

`multi_agent_graph.py` 从 `tool_calls_log`（reducer，跨轮累积）中提取 search_results。当 fallback 返回空结果时：

1. `search_results=[]` → 进入 tool_calls_log 提取分支
2. tool_calls_log 包含前两轮的 product_search 记录
3. 提取出 prod_055、prod_056 → 作为当前轮的 search_results
4. `if search_results:` 判断为 True → 发送 results 事件
5. 前端渲染出"嘴上说没找到，身体却推了包"的诡异现象

**解决方案**: 三层修复。

### 1. preprocessing.py — Task Switch 彻底清空上下文

当 `_detect_task_switch` 返回 True 时，不继承任何字段（包括 category/product_type/brand），只使用当前轮 entity_extractor 提取的结果：

```python
if task_switched:
    _ALL_CONTEXT_FIELDS = ("category", "product_type", "brand",
                           "scenario", "price_min", "price_max",
                           "soft_requirements", "hard_constraints")
    for field in _ALL_CONTEXT_FIELDS:
        if not entities.get(field) and prev_entities.get(field):
            entities[field] = None if not isinstance(prev_entities.get(field), list) else []
    logger.info("context_inheritance_cleared", reason="task_switch", cleared_fields=...)
```

### 2. agent_tools.py — ask_clarification 新增 search_failed 参数

当 Agent 搜索 0 结果时，传入 `search_failed=true`，函数强制返回 `should_ask=true` 并生成引导用户放宽条件的追问：

```python
async def ask_clarification(entities, asked_fields, search_failed=False):
    if search_failed:
        return {"should_ask": True, "strategy": "ask",
                "reason": "搜索无结果，需要用户放宽条件",
                "question_type": "search_failed_relax", ...}
```

react_prompt.py 新增决策规则：`product_search 返回 0 且 constraint_relaxation 已无效 → 调用 ask_clarification(search_failed=true)`

### 3. multi_agent_graph.py + shopping_agent.py — 清理僵尸 product_ids

两处修改：

a) tool_calls_log 提取增加 `used_fallback` 检查 — 当 fallback 触发时，不从跨轮累积的 tool_calls_log 中提取旧数据：
```python
if not search_results and not used_fallback:  # 新增 and not used_fallback
    for entry in tool_log: ...
```

b) results 事件发送增加 `and recommendations` 检查（multi_agent_graph.py 对齐 shopping_agent.py）：
```python
if search_results and recommendations:  # 原来只有 if search_results:
```

**修改文件**:

| 文件 | 改动 |
|------|------|
| `src/graph/preprocessing.py` | task_switch 时清空所有上下文字段，不再继承 category/product_type/brand |
| `src/tools/agent_tools.py` | `ask_clarification` 新增 `search_failed` 参数，搜索失败时强制追问 |
| `src/agents/react_prompt.py` | 新增决策规则：搜索失败且约束放宽无效时调用 `ask_clarification(search_failed=true)` |
| `src/graph/tool_executor.py` | `ask_clarification` 日志新增 `search_failed` 字段 |
| `src/graph/multi_agent_graph.py` | tool_calls_log 提取增加 `used_fallback` 检查 + results 发射增加 `and recommendations` |
| `src/graph/shopping_agent.py` | tool_calls_log 提取增加 `used_fallback` 检查 |

**修复后预期流程**:

```
第一轮: "新秀丽/通勤/预算700"
  → entities: {brand: "新秀丽", price_max: 700, scenario: "通勤"}
  → product_search → prod_055 ✓

第二轮: "国家地理/大容量/排除小米"
  → task_switch? No (scenario 未变) → 继承 brand
  → Agent 自我纠偏 → prod_056 ✓

第三轮: "先不看包了，看300元送礼好物"
  → task_switch! (scenario: "通勤"→"生日送礼")
  → 清空 category/product_type/brand/scenario/price_max
  → entities: {scenario: "生日送礼", price_max: 300}（仅当前轮提取）
  → product_search → 无国家地理硬过滤 → 找到送礼商品 ✓
  → 如果仍 0 结果 → ask_clarification(search_failed=true) → should_ask=true → 追问用户
  → 不再渲染前两轮的僵尸商品卡片
```

**状态**: 已修复

---

## P31: SSE 流式传输是"伪流式" — results/explanation 在图执行完毕后才一次性推送

**发现时间**: 2026-05-21

**现象**: 前端虽然通过 SSE 接收数据，但用户看到的效果是 loading → 长时间等待 → 产品卡片+推荐理由同时出现。中间只有 `intent`、`entities`、`tool_call` 等对用户无实际价值的中间事件，没有真正的"流式"体验。

**根因**: 三层阻塞叠加：

1. **results 延迟推送**：agent 节点完成时搜索结果已在 output 中，但代码在 `astream_events` 循环结束后、通过 `await graph.aget_state(config)` 取最终 state 时才提取和发送
2. **explanation 未逐 token 流式**：`LLMClient.chat()` 使用 `resp.json()` 非流式方式，`final_response` 作为完整文本一次性返回
3. **aget_state 阻塞**：`astream_events` 循环结束后额外调用 `aget_state` 提取最终状态，增加一次等待

**解决方案**: 分两步改进流式事件推送层，不改动图节点内部逻辑：

**Step 1: results 提前 yield**
在 `astream_events` 循环中，检测到 agent 节点完成（`on_chain_end`）时，从 output 的 `tool_calls_log` 中提取搜索结果并立即 yield `results` 事件。

**Step 2: explanation 逐 token 流式**
- `LLMClient` 新增 `chat_stream()` 方法，使用 `httpx.stream()` + SSE 解析实现流式 LLM 调用
- 图执行完成后，用 `chat_stream()` 发起新的流式 LLM 调用生成推荐总结
- 逐 token yield `explanation_delta` 事件，前端拼接实现打字机效果
- 同时保留 `explanation` 兼容事件

**Step 3: 移除 aget_state 阻塞**
在 `astream_events` 循环中通过 `collect_state_from_events()` 直接收集 state，不再额外调用 `aget_state`。

**前端改动**:
- `useChatStream.ts`: 新增 `explanation_delta` 事件处理（追加 delta），新增 `isStreaming` 状态字段
- `ChatBox.tsx`: 添加 `StreamingCursor` 组件（闪烁光标），流式过程中在内容末尾显示

**改动文件**:
- `src/models/llm_client.py` — 新增 `chat_stream()` 方法
- `src/graph/stream_utils.py` — 新增流式辅助函数
- `src/graph/multi_agent_graph.py` — 修改 `run_multi_agent_stream()`
- `src/graph/shopping_agent.py` — 修改 `run_agent_stream()`
- `frontend/src/hooks/useChatStream.ts` — 处理新事件 + isStreaming
- `frontend/src/components/ChatBox.tsx` — 打字光标

**状态**: 已修复

---

## P32: 搜索"男鞋"返回裤子/夹克 — 品类白名单缺少"鞋靴"导致全链路偏差

**发现时间**: 2026-05-22

**现象**: 用户搜索"男鞋"，返回的是裤子、夹克等服饰商品，没有鞋子。`product_search` 结果为 0 exact、10 supplemental，全部是服装。

**根因**: 三层缺陷叠加：

1. **实体抽取器品类白名单缺少"鞋靴"**（主因）：`entity_extractor.py` 的 system prompt 限制品类为 `护肤、奶茶、数码、服饰、箱包、食品、家居、母婴、运动`，"鞋靴"不在其中。LLM 被迫将"男鞋"归类为 `category: "服饰"`，导致后续过滤全偏。

2. **filter_builder 的 `_PRODUCT_TYPE_MAP` 缺少通用词"鞋子"**：Map 中有"运动鞋"、"皮鞋"等具体类型，但没有通用的"鞋子"。当 `category="服饰"` + `product_type="鞋子"` 时，Layer 1a（product_type 精确匹配）失败，Layer 1b（category 匹配）将"服饰"扩展为所有服装品类，鞋子被过滤掉。

3. **ranker 的 product_type 匹配过于严格**：`_score_product_type_match` 只做精确匹配和子串匹配，"鞋子" != "运动鞋" 且 "鞋子" 不在商品名中，所有商品都得到 0.1 分（乘法惩罚），排序接近随机。

**执行流程还原**:
```
用户: "男鞋"
→ entity_extractor: {category: "服饰", product_type: "鞋子", gender: "男"}
→ filter_builder: "服饰" → ["男装/", "女装/"] → 过滤掉"鞋靴/"品类
→ Qdrant: MatchAny(["男装/上装", "男装/下装"]) → 只返回服装
→ ranker: product_type_match=0.1（全部）→ 排序随机
→ product_search: 0 exact, 10 supplemental（全是裤子/夹克）
```

**数据验证**: `mock_data.json` 包含 6 款鞋子（prod_063~068），品类为"鞋靴/运动鞋"和"鞋靴/皮鞋"，但因过滤器排除了"鞋靴/"前缀，这些商品永远无法被检索到。

**解决方案**:

1. **entity_extractor.py**：品类白名单添加"鞋靴"
   ```
   品类只限：护肤、奶茶、数码、服饰、鞋靴、箱包、食品、家居、母婴、运动
   ```

2. **filter_builder.py**：`_PRODUCT_TYPE_MAP` 添加通用鞋类词
   ```python
   "鞋子": ["鞋靴/运动鞋", "鞋靴/皮鞋"],
   "男鞋": ["鞋靴/运动鞋", "鞋靴/皮鞋"],
   "女鞋": ["鞋靴/运动鞋", "鞋靴/皮鞋"],
   ```
   这样 Layer 1a（product_type 精确匹配）在 Layer 1b（category 匹配）之前命中，正确返回鞋靴品类。

3. **ranker.py**：`_score_product_type_match` 添加模糊匹配
   ```python
   # "鞋子" 的尾字 "鞋" 出现在 "运动鞋" 中 → 0.7 分
   core = product_type[-1]
   if core in p_type:
       return 0.7
   ```

4. **product_search.py**：`_split_matches` 阈值从 0.9 降到 0.7，使模糊匹配的商品归入 exact 而非 supplemental。

**改动文件**:
- `src/agents/entity_extractor.py` — 品类白名单添加"鞋靴"
- `src/retrieval/filter_builder.py` — `_PRODUCT_TYPE_MAP` 添加鞋子/男鞋/女鞋
- `src/agents/ranker.py` — `_score_product_type_match` 添加尾字模糊匹配
- `src/tools/product_search.py` — `_split_matches` 阈值 0.9→0.7

**状态**: 已修复

---

## P33: "男鞋推荐" 路由到 search_agent — intent_samples 推荐样本错误归类

**发现时间**: 2026-05-22

**现象**: 用户提问"男鞋推荐"，系统路由到 `search_agent` 而非 `recommend_agent`。同理，"推荐几款男鞋"、"有什么好用的护肤品推荐"等推荐类请求也被路由到搜索Agent。

**根因**: `data/intent_samples.json` 中，多个推荐类样本被错误放在 `"search"` 意图下：

```
search 意图中混入的推荐样本:
  "有什么好用的护肤品推荐"
  "推荐一款运动鞋"
  "有什么好喝的饮料"
  "帮我推荐一款面霜"
  "推荐一款口红"
```

语义路由器基于 embedding 相似度分类意图。当用户说"男鞋推荐"时，与 search 意图下的"推荐一款运动鞋"相似度最高，因此被分类为 `intent="search"`。而 `specialized_agents.py` 的 `INTENT_TO_AGENT` 映射中 `search → search_agent`，导致路由错误。

**路由链路还原**:
```
用户: "男鞋推荐"
→ semantic_router: "推荐一款运动鞋" (search intent) 相似度最高
→ intent = "search"
→ resolve_agent: INTENT_TO_AGENT["search"] = "search_agent"
→ 错误路由到 search_agent
```

**解决方案**: 修正 `data/intent_samples.json`，将推荐类样本从 `"search"` 移到 `"recommend"` 意图：

### 从 search 移除的样本（5个）
- "有什么好用的护肤品推荐"
- "推荐一款运动鞋"
- "有什么好喝的饮料"
- "帮我推荐一款面霜"
- "推荐一款口红"

### recommend 新增的样本（7个）
- 以上 5 个从 search 移入
- "男鞋推荐"（新增）
- "推荐几款男鞋"（新增）

### 重建语义路由索引

修改 intent_samples.json 后需重建 Qdrant 中的意图索引：
```python
from src.router.semantic_router import build_intent_index
count = await build_intent_index()  # 77 samples (原 75)
```

**修复后效果**:
```
用户: "男鞋推荐"
→ semantic_router: "推荐几款男鞋" (recommend intent) 相似度最高
→ intent = "recommend"
→ resolve_agent: INTENT_TO_AGENT["recommend"] = "recommend_agent"
→ 正确路由到 recommend_agent
```

**改动文件**:
- `data/intent_samples.json` — 推荐样本从 search 移到 recommend + 新增鞋类推荐样本
- Qdrant `intent_samples` collection — 重建索引（77 samples）

**状态**: 已修复

---

## P0-2 复盘：三个核心 bug 在 multi_agent 主路径的落地（2026-07-22）

**背景**：此前 P0/P1/P2 的修复大多只落在 legacy `shopping_graph.py`（node_rank 后过滤 + scenario_filter 接入）。但当前默认路径是 **multi_agent（product_search / multi_query_search 工具 → hybrid_search → rank）**，该路径存在缺口：

- **P0 预算**：`build_filter` 有 price Range，检索层能过滤；但排序后**无兜底后过滤**——实体没抽到 price_max 或过滤未生效时，超预算商品直接泄漏。
- **P1 场景**：`product_search` 根本没调 `filter_by_scenario`，rank 只做软加权 boost，不会硬排除奶茶/饮品 → 送礼仍可能推蜜雪冰城。
- **P2 品类**：`build_filter` 已生成品类条件，OK。

**修复**：新增 `_post_filter(products, entities)`（`src/tools/product_search.py`），排序后、截断前施加两道硬过滤：
1. 预算：剔除 `price > price_max` 或 `price < price_min` 的商品（兜底，独立于检索层过滤）。
2. 场景：复用 `filter_by_scenario` 按品类白/黑名单剔除（如礼物 → 排除奶茶/食品/家居）。

`product_search` 和 `multi_query_search` 两个工具均已接入。

**回归测试**：`tests/test_p02_core_filters.py`（15 条，确定性、不依赖 LLM/Qdrant/Ollama）：
- 预算：`price_max=500` 剔除 ¥549、保留边界 ¥500、支持 price_min、双边界
- 场景：生日礼物排除奶茶/食品/家居、保留护肤/数码、无场景透传
- 组合：场景 + 预算同时施加
- build_filter：品类条件存在、price Range(lte/gte) 正确、空实体返回 None

**状态**: 已修复

**遗留（非本次范围）**：`tests/test_tools.py` 有 4 个既有失败（`test_all_six_tools_registered` 硬编码期望 6 工具但实际 7 个含 multi_query_search;ask_clarification / constraint_relaxation 行为漂移），与 P0-2 无关,建议单独处理。
