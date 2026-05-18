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
