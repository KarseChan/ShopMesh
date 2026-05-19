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
