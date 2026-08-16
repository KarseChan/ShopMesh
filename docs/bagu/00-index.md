# 校招后端八股 · 学习总索引

> 定位:**应届/秋招后端**,语言不限(以**跨语言通用原理** + **Java/Go/Python 运行时差异对照**为主)。
> 目标:能照着学、讲清原理、扛住高频追问。不深挖生产调优(那是社招)。
> 用法:每篇按「概念 → 为什么这么设计 → 面试怎么答 → 高频追问」组织。先扫本页 Top 清单定优先级,再逐篇精读。

## 文件
1. [01 计算机网络 + 操作系统](01-network-os.md)
2. [02 MySQL + Redis](02-mysql-redis.md)
3. [03 并发编程 + 语言运行时(JVM/GIL/GMP 对照)](03-concurrency-runtime.md)
4. [04 分布式 + 消息队列 + 系统设计](04-distributed-mq-design.md)
5. [05 大厂电商/秒杀实战案例 → 反查八股](05-cases-ecommerce-seckill.md) ← 用真实场景串联 01–04 的八股
6. [06 大厂推荐/Feed 流实战案例 → 反查八股](06-cases-recsys-feed.md) ← 最贴你项目,含 recsys/agent 边界的工业锚点(RecGPT)
7. [07 大厂 IM/中间件实战案例 → 反查八股](07-cases-im-middleware.md) ← IM 可靠投递/红包 + RPC/注册中心/网关(讲清你的微服务+Traefik)

---

## 校招后端「必背」Top 高频清单(答不上直接减分)

**计网**
- [ ] TCP 三次握手/四次挥手 + 为什么 3 次不是 2 次 + TIME_WAIT
- [ ] TCP 如何保证可靠 + 拥塞控制(慢启动/拥塞避免/快重传快恢复)
- [ ] TCP vs UDP
- [ ] HTTP/1.1 vs 2 vs 3;常见状态码;GET vs POST
- [ ] HTTPS/TLS 握手(对称+非对称+证书链)
- [ ] 强缓存 vs 协商缓存
- [ ] 输入 URL 到页面显示全过程

**操作系统**
- [ ] 进程 vs 线程 vs 协程;上下文切换开销
- [ ] 死锁四条件 + 预防
- [ ] 虚拟内存 / 分页 / 缺页 / 页面置换
- [ ] 用户态/内核态、系统调用
- [ ] IO 多路复用 select/poll/epoll 区别 + ET/LT
- [ ] 五种 IO 模型 / 同步异步阻塞非阻塞

**MySQL**
- [ ] 为什么用 B+ 树做索引;聚簇索引/回表/覆盖索引/最左前缀
- [ ] 事务 ACID + 四隔离级别 + 脏读/不可重复读/幻读
- [ ] MVCC 原理;RR 怎么(部分)解决幻读
- [ ] redo/undo/binlog 各干嘛 + 两阶段提交
- [ ] 锁:行锁/间隙锁/临键锁;索引失效场景

**Redis**
- [ ] 5 大数据结构 + 底层实现;为什么快
- [ ] RDB vs AOF;过期删除 + 内存淘汰策略
- [ ] 缓存穿透/击穿/雪崩 + 解法
- [ ] 缓存与 DB 一致性(Cache Aside / 延迟双删)
- [ ] 分布式锁(SETNX + 过期 + 唯一 value + Lua)

**并发 + 运行时**
- [ ] 线程安全三要素:原子/可见/有序
- [ ] 乐观锁 CAS + ABA;悲观锁;死锁
- [ ] 线程池参数 + 拒绝策略 + 为什么用
- [ ] (选定语言)Java: JMM/volatile/synchronized/AQS/ConcurrentHashMap + JVM 内存区域/GC/类加载双亲委派;或 Python: GIL/asyncio/引用计数GC;或 Go: GMP/channel/三色标记

**分布式 + MQ + 设计**
- [ ] CAP + BASE + 最终一致
- [ ] 分布式事务:2PC/TCC/Saga/本地消息表/事务消息
- [ ] 分布式 ID(雪花算法)
- [ ] 限流(令牌桶/漏桶/滑动窗口) + 熔断降级
- [ ] MQ 作用 + 消息丢失/重复/顺序/积压 + 投递语义
- [ ] 一致性哈希;Raft 选主+日志复制(会讲即可)
- [ ] 系统设计方法论 + 一道经典题(短链/秒杀)

---

## 语言差异「一句话」速查(不限语言时的对照锚点)

| 维度 | Java | Python | Go |
|---|---|---|---|
| 并发单位 | 线程(1:1 OS 线程) | 线程(受 GIL 限)/ 协程(asyncio) | goroutine(M:N 协程) |
| 并行能力 | 真多核并行 | **GIL 下同一时刻只一线程执行字节码**;多核靠多进程 | 真多核并行 |
| 内存/GC | 分代 + 可达性分析(G1/ZGC) | **引用计数** + 分代(处理循环引用) | **三色标记** + 写屏障,并发 GC |
| 同步原语 | synchronized/Lock/JUC/CAS | Lock/Queue/asyncio.Lock | channel(CSP)/sync 包 |
| 典型坑 | 内存泄漏、GC 停顿 | GIL 使 CPU 密集失效、需多进程 | goroutine 泄漏、channel 死锁 |

> 面试若被问"你项目 Python 有 GIL 怎么办":IO 密集(网络/DB/LLM 等待)用 asyncio/多线程照样并发;CPU 密集用多进程或交给 C 扩展。**我的 agent 是 IO 密集(等 LLM/向量库),asyncio 就够。**
