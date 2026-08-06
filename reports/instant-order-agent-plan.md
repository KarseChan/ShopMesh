# 方案:对话式秒送点单 Agent(外卖 / 附近店即时配送)

> 在现有「导购 + 交易」agent 上,新增**对话式即时点单**:自然语言 → 多约束(预算 / 时效 / 口味 / 距离 / 忌口)→ 就近召回 → 组单下单。
> **核心定位:agent 只做"对话式约束求解 + 下单编排"这一层;毫秒级 feed 推荐仍是确定性 recsys。** 这条边界本身就是方案最值钱的部分。
> 关系:本方案取代 [travel-vertical-plan.md](travel-vertical-plan.md) 成为推荐的扩展方向(地理检索部分两者复用)。

---

## 一、为什么是这个方向(而非旅游 / feed 推荐)

- **几乎白捡现有系统**:`products` 表已有 `delivery_minutes`、`platform_id`,ranker 已有时效维,购物车/订单/支付/退款闭环已就绪。秒送只多两样:**geo(附近店)** + **即时时效/营业中硬约束**。
- **踩在 2025 产业前沿,不烂大街**:美团 2025.9 上线首个 AI Agent「小美」(自然语言点外卖,背后 LongCat-Flash 560B MoE);阿里龙猫、淘宝问问、京东言犀同类。做对话式点单 agent 是跟真实趋势,不是复刻教程。
- **强化你最强的判断信号**:*知道 recsys 与 agent 的边界*。和你已有的"砍 DAG / LLM 离线富化、在线毫秒服务"同一个 thesis——**加它是强化差异化,不是堆功能**。

---

## 二、recsys / agent 边界(方案的灵魂,先讲清)

| 能力 | 归属 | 为什么 |
|---|---|---|
| 首页/列表 feed 推荐(高 QPS、毫秒级) | **确定性 recsys(不做进 agent)** | LLM 进不了毫秒在线路(你的网关 16s 就是证据);美团小美也只在对话层,feed 仍是级联 recsys |
| 就近召回 + 硬过滤(geo/营业中/时效/预算) | **确定性代码**(下推 Qdrant/规则) | 约束是可判定的,不需要也不该用 LLM |
| 多目标排序 | **确定性 ranker** | 已有多目标排序,加距离/时效/配送费维即可 |
| 自然语言 → 结构化多约束 | **agent(LLM,规则快路径优先)** | 口语"30 块内、半小时送到、微辣不要香菜"→ 槽位,LLM 才擅长 |
| 组单 / 再来一单 / 多约束凑单 | **agent 编排** | 多 item + 预算/时效权衡,多步规划,LLM 有增量价值 |
| 下单 / 支付 | **确定性 commit + 人工闸门** | 复用你的交易范式,LLM 绝不自主花钱 |

> 简历一句话:"核心秒送推荐我用确定性 recsys(生产就是这么做,小美也印证 agent 只在对话层);agent 只负责对话式约束求解与下单编排。我清楚为什么不在毫秒级 feed 里塞 LLM。"

---

## 三、相对现有购物系统的新维度

现有 `products` 是"无位置、无时段、时效字段闲置"。秒送激活三件事:

1. **地理可送达(LBS)**:门店有经纬度 + 配送范围;推荐必须满足 `distance(用户, 门店) ≤ 配送半径`。距离进排序。
2. **即时时效 / 营业状态**:门店有营业时间(**营业中**=硬过滤)、预计送达 ETA(≤用户要求=硬/软约束)。`delivery_minutes` 从"闲置字段"变成一等排序信号。
3. **对话式多约束点单**:预算 / 时效 / 口味(微辣) / 忌口(不要香菜) / 品类(午饭) / 距离,混在一句口语里 → agent 抽槽 + 权衡 + 组单。

---

## 四、架构映射(逐子系统,最大化复用)

### 4.1 数据 & Schema(Flyway 唯一 SoT)

新增门店表(`V6__merchants.sql`),商品/菜品 MVP 先复用 `products` 加 `merchant_id`:

```sql
-- merchants: id, merchant_id, name, city, latitude, longitude,
--   category(快餐/奶茶/超市/药店...), rating, avg_price, delivery_fee,
--   delivery_minutes(ETA), delivery_radius_km, open_hours(text),
--   tags(text 微辣/清真/素/网红...), image_url, embedding_text
-- products 增列: merchant_id(可空,秒送商品挂到门店), item_type('good'|'dish')
```

- 不塞多态大宽表;门店独立成表,`products` 只加两列(平滑)。SQLModel 同步,不驱动迁移([MIGRATIONS.md](../docs/MIGRATIONS.md))。
- Mock:`scripts/generate_mock_merchants.py` 生成 N 城若干门店(真经纬度 + 营业时间 + ETA + 菜品)。

### 4.2 检索:geo + 营业中 + 时效 下推(核心新增)

- Qdrant 门店/菜品 payload 带 `location:{lat,lon}`、`open_now`(或营业时段)、`eta`、`item_type`。
- `build_filter` 新增**下推到 Qdrant 的硬过滤**(和我做过的"场景白名单下推"同手法,HNSW 只遍历可行点,避免事后过滤打空):
  - `GeoRadius(用户位置, 半径)` —— 只召回可送达门店
  - `open_now == true` —— 只召回营业中
  - `eta ≤ max_delivery_minutes`(若用户给了时效)
  - 复用现有 `price ≤ budget`
- haversine 距离 + ETA 回填为排序信号。

### 4.3 实体新增

`entity_extractor` + **规则快路径**(继续无 LLM 抽,延迟主线不变)新增:
`user_location`(经纬度/地址)、`max_delivery_minutes`(时效)、`meal_type`(早/午/晚/夜宵)、`taste_tags`(微辣/清淡...)、`dietary`(忌口:不要香菜 / 清真 / 素食)、`merchant_category`(快餐/奶茶/药店)。
例:"点份 30 块内半小时到的微辣午饭,不要香菜" → budget=30, max_delivery_minutes=30, meal_type=午, taste_tags=[微辣], dietary=[无香菜]。

### 4.4 工具(复用 product_search 模式:hybrid + rank + post_filter + 空结果放宽)

- `nearby_merchant_search(location, entities, semantic_query)` —— 秒送版检索(geo/营业中/时效/预算 + 口味语义)。
- **`reorder_from_history(user_id)`** —— **"再来一单"**:拉高频/上次订单,直接重组购物车。天然个性化 + agentic,外卖经典。
- **`assemble_meal(constraints)`**(P3)—— 预算/时效内**多菜凑单**(主食+饮品),多步权衡,LLM 增量价值所在。
- 下单/支付/退款:**直接复用**你的 cart/order/payment 工具与 SENSITIVE 拦截,不重写。

### 4.5 排序:时效优先 profile + 距离/配送费维

- ranker 新增维度:`distance`(近↑)、`eta`(快↑,复用 delivery_minutes)、`delivery_fee`(低↑)。
- 新 rank profile `instant_delivery`(时效/距离权重高),和现有 `price_sensitive`/`quality_sensitive` 同机制;"急"类 query(马上/尽快)自动选它。

### 4.6 意图 & Agent

- 新意图 `instant_order`(点外卖/附近秒送)、`reorder`(再来一单)。确定性路由沿用 if/else。
- Agent:MVP 先扩 `search_recommend_agent` 工具集;成熟后拆 `instant_order_agent`(独立 prompt + 秒送工具子集 + 迭代预算)。
- `instant_order` → 缺 `user_location`/时效时走 `ask_clarification`(复用);组单走 `assemble_meal`。

### 4.7 交易 & 履约(几乎全复用)

- 下单 = 你的确定性 commit + 库存原子预占 + 幂等 + 状态机。
- 履约状态机扩展:`备餐 → 配送中 → 已送达`(沿用订单状态机 + Celery 超时对账,你已有)。
- 支付:沙箱 + HMAC 验签 webhook,原样复用。

### 4.8 记忆 / 个性化

- 画像扩展:常点品类、口味标签、忌口、常用地址;向量长期记忆复用 `user_long_term_memories`。
- "再来一单"、时段推荐(早餐/夜宵)由画像 + 时段驱动。

---

## 五、分阶段落地

| 阶段 | 内容 | 交付 | 顺序理由 |
|---|---|---|---|
| **P1(MVP)** | 就近约束检索打通 | `V6__merchants` + mock + Qdrant geo/营业中/时效 下推 + `nearby_merchant_search`(含放宽)+ `instant_delivery` profile + `instant_order` 意图 + 时效/忌口实体 | 先验证 geo+时效硬约束这条最新的链路 |
| **P2** | 点单闭环 | 组单 → 下单 → 支付(复用)+ 履约状态机(备餐/配送中/送达) | item 就绪后接你现成的交易范式 |
| **P3** | **agentic 亮点** | `reorder_from_history`("再来一单")+ `assemble_meal`(预算内凑单) | 前两阶段就绪后,agent 才能编排 |
| **P4** | LBS 个性化 + 时段 | 常点/口味画像、早午晚夜宵时段推荐 | 锦上添花 |
| 贯穿 | **Eval 扩展** | `eval_recommendation.py` 加秒送用例:送达可行率(geo)、时效达成率、预算/忌口命中 | 延续数据驱动主线 |

---

## 六、差异化 & 简历叙事

- **recsys/agent 边界判断**(最强):明确"feed=确定性 recsys,对话点单=agent",并能引 2025「小美」印证。同时懂产业 + 有判断,稀缺。
- **geo 硬约束下推向量库**:可送达/营业中/时效在 Qdrant 预过滤,复用你的"白名单下推"手法。
- **让闲置的 `delivery_minutes` 变成一等信号 + 时效优先 profile**:小而具体的排序工程。
- **交易范式可迁移**:外卖下单/履约直接套购物的人工闸门 + 幂等 + 验签 + 超时对账。
- **"再来一单" / 凑单 agent**:LLM 多步规划真正有价值的点(单查询确定性即可,组单才用 LLM)。

## 七、风险 / 取舍(诚实)

- **不做真实调度/骑手 ETA**:mock ETA + 固定配送费,和你的沙箱支付同思路;真实履约是重系统,不碰。
- **geo 用 mock 经纬度**,不接地图 API(过度设计)。
- **不做高 QPS feed**:那是确定性 recsys 的活,也是本方案刻意不碰的边界。
- **慢网关**:对话式点单能容忍秒级延迟(feed 不能——这正是 agent 只该在对话层的原因);继续规则快路径 + 少调 LLM,`assemble_meal` 这种多步才用 LLM。

---

## 参考

- [美团首个 AI Agent「小美」实测(腾讯新闻)](https://news.qq.com/rain/a/20250914A03QQD00)
- [美团"小美"公测(科技日报)](https://www.stdaily.com/web/gdxw/2025-09/12/content_399908.html)
- [美团餐饮商家 AI 产品全图(新浪财经)](https://client.sina.com.cn/news/2025-10-16/doc-infucfih9409659.shtml)
- [美团技术团队:搜索/推荐级联架构](https://tech.meituan.com/)
