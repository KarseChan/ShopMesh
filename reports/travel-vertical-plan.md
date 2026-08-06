# 方案:ShopMesh 扩展旅游垂类(酒店 + 景点)

> 目标:在现有「物品推荐」agent 基础上,新增**酒店 / 景点**推荐,参考美团旅游本地生活的做法。
> 原则:**复用现有单一执行路径 + 工具化 + 混合检索 + 多目标排序**,只在必要处引入旅游领域特有能力(地理/日期/异构),**分阶段落地,先做 MVP 别一次性堆全**。

---

## 一、美团调研要点(指导设计)

| 要点 | 含义 | 对本方案的启示 |
|---|---|---|
| 多阶段级联排序 | 召回→粗排→精排→重排→**异构混排** | ShopMesh 现在是单趟 rank;旅游先不上级联,但要预留**异构排序**(不同品类同列) |
| **地理可行性=硬约束** | 用户/POI 坐标、时空信号进 geo-aware 排序;不可行的地理位置直接过滤 | 地理半径过滤要**下推到 Qdrant 预过滤**(和我做过的"场景白名单下推"同思路) |
| 旅游形式多样 | 景点、跟团游、**景酒套餐** | 差异化点在**行程/套餐规划**——LLM agent 真正有价值的地方 |
| 本地 vs 异地 | 本地用户看"附近周末去处",异地用户看"目的地攻略" | 意图/召回要区分 `local` vs `destination` |
| 季节性 | 景点有淡旺季、时令 | 排序加"季节匹配"维度(P4 再做) |
| LLM agentic 本地生活推荐 | 学术界已在用 agent 做本地生活推荐 | 印证 ShopMesh 的 agent 定位;**行程规划 agent** 是最强叙事 |

---

## 二、旅游垂类 vs 物品:四个本质差异

现有 `products` 是"无位置、无时间、即买即得"的实物。酒店/景点引入四个新维度:

1. **地理(LBS)**:酒店/景点有经纬度、城市、商圈。推荐必须满足**地理可行性**(离用户/目标位置多远),距离本身是强排序信号。实物没有。
2. **时间与可用性**:酒店有入住/离店日期、按夜计价、房态;景点有开放时间、门票日期。实物没有。
3. **异构 item**:同一"目录"里现在有实物 / 酒店 / 景点,**属性各不相同**(酒店有星级/房型,景点有门票/时长)。需要多态 item 模型 + 类型感知的检索与排序。
4. **可组合(行程)**:景酒套餐、多景点行程——把多个 item **规划成一条行程**,这是 agent 的增值点(单 item 搜索用不到 LLM 规划,行程才用得到)。

---

## 三、架构映射(逐子系统,复用现有模式)

### 3.1 数据 & Schema(Flyway 唯一 source of truth)

新增两张表(Flyway `V6__hotels.sql` / `V7__attractions.sql`),字段只取 MVP 必需:

```sql
-- hotels: id, hotel_id, name, city, district, latitude, longitude,
--         star_rating, price_per_night, rating, tags(text), image_url, embedding_text
-- attractions: id, poi_id, name, city, district, latitude, longitude,
--         category(自然/历史/亲子/网红...), ticket_price, rating, open_hours,
--         suit_tags(亲子/情侣/adventure...), season(text), image_url, embedding_text
```

- 与现有 `products` 并列,**不塞进 products 表**(避免稀疏多态列)。SQLModel 加对应模型(读写用,与 Flyway 保持一致,不驱动迁移——见 [MIGRATIONS.md](../docs/MIGRATIONS.md))。
- Mock 数据:`scripts/generate_mock_pois.py` 生成 N 个城市的酒店/景点(带真经纬度)。

### 3.2 检索:统一 item 抽象 + 地理预过滤(核心新增)

- **Qdrant**:新增统一集合 `pois`(或复用 `products` 的检索管线),payload 带 `item_type`(hotel/attraction)+ `location:{lat,lon}` + 类型字段。`embedding_text` 各类型自建。
- **`hybrid_search` 扩展**:在 `build_filter` 里新增两类**下推到 Qdrant 的硬过滤**——
  - `FieldCondition(key="item_type", MatchValue)`(只召回该类型)
  - `GeoRadius`(在目标位置半径内)——**和我做的场景品类白名单下推同一手法**,HNSW 只遍历地理可行的点,而非事后过滤打空。
- 距离(haversine)作为一个新的排序信号回填。

### 3.3 实体(entities)新增旅游槽位

`entity_extractor` + 规则快路径新增:
- `item_type`(hotel/attraction/good)、`city` / `near`(地理目标)、`check_in`/`check_out`/`nights`、`star_rating`、`guests`、`travel_tags`(亲子/情侣/网红/自然/历史/adventure)、`trip_type`(local/destination)。
- 规则快路径要能从"上海亲子游玩两天"抽出 city=上海、travel_tags=[亲子]、nights=2、trip_type=destination——**继续走无 LLM 快路径**,别每句都调慢模型(延迟主线不变)。

### 3.4 工具(Tools)新增

复用 `product_search` 的模式(hybrid + rank + post_filter + **空结果自动放宽**):
- `hotel_search(entities, semantic_query)` / `attraction_search(...)`——或一个泛化的 `poi_search(item_type, ...)`。
- `nearby_search(location, radius, item_type)`——地理召回。
- **`itinerary_plan(city, days, budget, tags)`**——**差异化王牌**:agent 编排「选景点→就近选酒店→按预算/时段组合成行程」,多步工具调用 + LLM 规划。这才是 LLM agent 相对传统召回的增量价值。
- 交易侧沿用现有闸门:酒店"下单"= 预订,仍走**人工确认 + 确定性 commit + 幂等**(复用你的订单/支付范式,SENSITIVE 拦截不变)。

### 3.5 排序:类型感知 profile + 距离维度

- `ranker` 新增维度:`distance`(越近越高)、`star_rating`、`season_match`(P4)。
- 新增 rank profile:`hotel_profile`(位置/评分/价格/星级权重)、`attraction_profile`(热度/评分/距离/标签匹配)——和现有 `price_sensitive`/`quality_sensitive` profile 同机制。
- **异构排序**(可选,P3+):若一条列表混合景点+酒店(如套餐),各类型分数需归一化后再融合(参考美团异构混排)。

### 3.6 意图路由 & Agent

- 新意图:`poi_search`(找酒店/景点)、`travel_planning`(行程/套餐)。确定性路由沿用你的 if/else 风格。
- 新 agent:`travel_agent`(独立 prompt + 旅游工具子集 + 迭代预算),或先扩 `search_recommend_agent` 的工具集(MVP 更省)。
- `travel_planning` → 走 `itinerary_plan` 多步循环(agent 真正发挥的场景)。

### 3.7 记忆 & 个性化

- 用户画像扩展:常去城市、偏好标签(亲子/网红)、价格档;向量长期记忆复用现有 `user_long_term_memories`。
- 本地/异地:用画像里的常驻城市 vs 当前 query 城市判断 `trip_type`。

---

## 四、分阶段落地(小步、可验证)

| 阶段 | 内容 | 交付 | 为什么这个顺序 |
|---|---|---|---|
| **P1(MVP)** | **景点**先做(无日期/房态,最简) | Flyway V7 + mock 数据 + Qdrant geo 集合 + `attraction_search`(含 geo 下推 + 空结果放宽) + `attraction_profile` + `poi_search` 意图 | 景点比酒店少了"日期/可用性"维度,验证 geo 检索 + 异构最快 |
| **P2** | **酒店**(加日期/星级/按夜价) | Flyway V6 + `hotel_search` + 日期实体 + `hotel_profile` | 在 P1 的 geo 底座上加时间维度 |
| **P3** | **行程/景酒套餐 agent**(差异化王牌) | `itinerary_plan` 工具 + `travel_planning` 意图 + `travel_agent` | 前两阶段的 item 就绪后,agent 才能编排组合 |
| **P4** | LBS 个性化 + 季节性 + 异构混排 | 本地/异地区分、`season_match` 维度、多类型同列归一 | 锦上添花,数据/流量足够再做 |
| 贯穿 | **Eval 扩展** | `eval_recommendation.py` 加旅游用例(geo 命中率/距离约束/标签匹配) | 延续你"数据驱动"的主线 |

---

## 五、差异化亮点 & 简历叙事

- **把 agent 扩到新垂类**:展示领域建模能力(实物→旅游的四个维度差异),而非只加功能。
- **geo-aware 检索**:地理半径**下推到向量库预过滤**(而非事后过滤打空),复用并印证你已做过的"场景白名单下推"手法。
- **行程规划 agent**:LLM agent 真正有增量价值的场景——单 item 搜索确定性即可,**多 item 行程编排才需要 LLM 规划**。这与"agentic 本地生活推荐"的前沿方向一致。
- **交易范式可迁移**:酒店预订直接复用你的"人工闸门 + 确定性 commit + 幂等 + 验签"交易设计。

## 六、风险 / 取舍(诚实)

- **别一次性做全**:真实房态/库存/日历定价是重系统,MVP **不接真实房态**,用 mock + 固定价,和你现在的沙箱支付同思路。
- **geo 数据**:mock 经纬度即可;别为了"真实"去接地图 API(过度设计)。
- **异构混排**先不做:P1/P2 各类型独立成列,等真有"混合列表"需求再上归一化。
- **仍是慢网关**:旅游 query 更长、实体更多,`product-group` 慢的问题依旧;继续走规则快路径 + 减少 LLM 调用,行程规划这种多步才用 LLM。

---

## 参考

- [美团搜索粗排优化的探索与实践](https://tech.meituan.com/2022/08/11/coarse-ranking-exploration-practice.html)
- [多业务建模在美团搜索排序中的实践](https://tech.meituan.com/2021/07/08/multi-business-modeling.html)
- [Enhancing Local Life Service Recommendation with Agentic Reasoning in LLM](https://arxiv.org/pdf/2604.14051)
- [Proximity-Aware Geo-Codebook for Local Service Recommendation](https://arxiv.org/html/2604.23156)
- [Deep Learning of Dynamic POI Generation for Itinerary Recommendation (ACM TORS)](https://dl.acm.org/doi/10.1145/3713079)
