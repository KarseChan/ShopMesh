# 购物(交易)功能设计方案

> 在现有"导购(推荐/浏览)"系统上,增加"购物车 → 下单 → 支付"的交易能力。
> 版本 v1 · 定位:agent 系统做交易的架构设计 + 分阶段落地。

---

## 0. 现状盘点(已有 / 缺失)

**已有**(交易骨架其实已经搭了一半):
- HITL 下单确认:`node_prepare_order → node_confirm_order`(LangGraph `interrupt("请确认下单")`),`/api/chat/order` 端点,前端 `OrderConfirm` 组件,`/api/chat/resume` confirm/cancel。
- `Order` 表(`src/db/models.py`,含 `order_id`)。
- 商品 `stock` 字段。
- 权限三级 `PermissionLevel`(READ/WRITE/SENSITIVE),`check_permission` 已强制(WRITE/SENSITIVE 需 `user_confirmed`)。
- Celery + Beat(可做超时取消/对账定时任务)、JWT 鉴权、structlog + data_guard 审计。

**缺失**:
- 购物车(只有单商品直接下单)。
- 订单状态机(有表,无生命周期)。
- 库存预占(有 stock 字段,无并发安全的 reserve/commit/release)。
- 支付(完全没有)。
- 购物车/订单相关的 agent 工具。
- 下单幂等。

---

## 1. 核心架构原则(最重要)

> **Agent 负责"提议"与"编排",绝不自主"花钱"。任何花钱动作 = 确定性代码执行 + 人工确认 + 服务端二次校验。**

**理由**:LLM 会幻觉、会被 prompt injection。若让 LLM 自主下单/支付,一句"把购物车全部下单"或一段注入文本即可造成资损。因此按操作风险分层,复用现有 `PermissionLevel`:

| 层级 | 操作 | 谁决定 | 现有对应 |
|------|------|--------|----------|
| **读** | 搜索/比价/看购物车/查订单 | Agent 自由调 | `PermissionLevel.READ` |
| **写** | 加购物车/改数量 | Agent 可调,幂等 + 可撤销 | `PermissionLevel.WRITE`(需 confirmed) |
| **敏感** | 下单/支付/退款 | **必须 HITL 人工确认 + 服务端校验** | `PermissionLevel.SENSITIVE` + `interrupt()` |

权限三级 + interrupt 已是正确骨架,交易功能挂在其上。

---

## 2. 组件设计

### 2.1 购物车 Cart
- 存储:Redis(会话级,快)为主,可异步落 Postgres(跨设备持久)。
- 结构:`{user_id, items: [{product_id, qty, price_snapshot, added_at}]}`
- 要点:存**价格快照**用于展示;**下单时用服务端实时价重新校验**(防快照过期/被改)。

### 2.2 订单状态机 Order
```
created → awaiting_payment → paid → shipped → completed
                │(超时/用户取消)          │
                ▼                         ▼
            cancelled                 refunded
```
- 状态流转:确定性代码 + DB 事务,**不经过 LLM**。
- 超时未支付:Celery Beat 定时扫描 → 自动 cancel + 释放库存。

### 2.3 库存预占 Inventory
- 下单 **reserve**(预占,带 TTL)→ 支付成功 **commit**(真正扣减)→ 取消/超时 **release**。
- 防超卖:Redis 原子 `DECRBY` 或 DB `SELECT ... FOR UPDATE` / 乐观锁(version 字段)。

### 2.4 支付 Payment(安全红线最集中)
> **绝对边界:后端与 agent 永不接触卡号 / CVV / 密码。**
- 用支付网关 **hosted checkout / payment intent**(支付宝/微信/Stripe):后端创建 payment session → 返回跳转 URL 或二维码 → 用户在**支付方页面**付款。
- 支付结果**只信 webhook**(支付方 → 后端异步回调),**不信前端回传**(可伪造),校验签名。
- 幂等 key 防重复下单/重复扣款。

---

## 3. 新增 agent 工具(接 `tool_registry` + `PermissionLevel`)

| 工具 | 权限 | 说明 |
|------|------|------|
| `add_to_cart` / `update_cart` / `remove_from_cart` | WRITE | 购物车增删改,幂等 |
| `view_cart` / `get_order_status` | READ | 查看 |
| `create_order` | **SENSITIVE** | 只生成**订单草稿** + 触发 `interrupt()` 人工确认 |
| `initiate_payment` | **SENSITIVE** | 返回支付链接/二维码,**不处理支付本身** |

Agent prompt 约束:下单/支付类工具仅在用户明确表达购买意图后调用,且必须经用户确认。

---

## 4. 完整下单 → 支付时序(扩展现有 interrupt HITL)

```
用户:"加购物车" → agent add_to_cart(WRITE)
用户:"结算"     → agent create_order(草稿, SENSITIVE)
   → interrupt("确认下单:X 商品 ¥Y,收货至 Z,确认?")     ← 现有能力
   → 前端 OrderConfirm 确认 → /api/chat/resume(confirm)
   → 服务端:重算价格 + 校验库存 → 预占库存 → 生成正式订单(幂等键)
   → initiate_payment → 返回支付链接/二维码
用户在支付方完成付款
   → 支付方 webhook → 后端校验签名 → 订单 paid + 提交库存扣减
   → (超时未付:Celery Beat 取消订单 + 释放库存)
```

现有 `node_prepare_order → node_confirm_order(interrupt)` 正是中间"确认"段,**往前接购物车、往后接支付**即可。

---

## 5. 关键工程点(面试深挖点)

1. **幂等**:下单用幂等键(cart hash 或客户端 UUID),同键重复请求返回同一订单 → 防网络重试重复扣款。
2. **并发/防超卖**:库存扣减原子化(Redis / 行锁 / 乐观锁)。
3. **只信 webhook**:支付状态以支付方 webhook 为准,校验签名,防伪造。
4. **服务端二次校验**:下单重算价格(不信购物车快照)、校验库存、校验用户身份(JWT)。
5. **审计**:所有 SENSITIVE 操作落审计日志(structlog + data_guard)。
6. **补偿/一致性**:支付成功但发货失败等,用状态机 + Celery 定时对账兜底。

---

## 6. 分阶段落地

| 阶段 | 内容 | 风险 |
|------|------|------|
| **P1 购物车** | Redis cart + `add_to_cart`/`view_cart`/`update_cart`/`remove_from_cart` 工具(READ/WRITE) | 低,先做 |
| **P2 订单** | Order 状态机 + 库存预占 + `create_order`(接现有 interrupt 确认) + 幂等键 | 中 |
| **P3 支付** | **沙箱/测试模式**(Stripe test / mock 支付):payment session + webhook + 超时取消(Celery Beat) | 中 |
| **P4 收尾** | 取消/退款 + 订单历史 UI | 中 |

⚠️ **Demo 红线**:绝不接真实支付 / 真实扣款。用支付方**测试沙箱**完整演示这套流程 —— 既安全,又能展示懂支付架构。

---

## 7. 面试叙事

> "我把系统分成**导购(读)**和**交易(写/敏感)**两层,核心判断是**不让 LLM 自主执行花钱动作**:LLM 只提议和编排,实际下单/支付走确定性代码 + 人工确认 + 服务端二次校验;支付用 hosted checkout + webhook,后端永不接触支付凭证;用幂等键防重复扣款、库存原子扣减防超卖、Celery 定时任务做超时取消和对账。"

这套"**LLM 编排 + 确定性 commit + 人工闸门**"是 agent 做交易的正确范式,比功能本身更能证明架构能力。
