# 记忆管理系统改进方案

> 目标：将当前"能用"的记忆系统升级为"可面试展示"的工程级记忆系统。
> 原则：渐进式改进，不破坏已有功能，每阶段可独立交付。

---

## 现状诊断

### 已有但未接通
- `SessionMemory`（L2a/L2b）已实现，但新 Agent graph **完全未调用**
- `compressor.py` 只被 `session_memory.py` 引用，新架构中处于闲置状态
- `context_assembler.py` 的 `window_messages` 和 `historical_summary` 参数在新架构中始终为空

### 已接通但能力不足
- L2c 向量记忆写入靠正则匹配（`should_save_memory`），覆盖面窄
- L2c 向量记忆召回靠触发词（`should_recall`），无语义召回
- L3 用户画像只有显式更新，无隐式反馈推断
- 无记忆遗忘/衰减机制，向量只增不减

### 架构断层
- Checkpointer 使用 `MemorySaver`（in-process），进程重启即丢失
- Session Memory（Redis）和 Checkpointer（in-memory）是两套独立系统，未协同
- 记忆写入在主链路上同步执行，增加了不必要的延迟

---

## 改进分 4 个阶段，按优先级排序

---

## Phase 1: 接通 Session Memory + 自定义 Checkpointer

### 1.1 在 preprocessing 中加载 Session Memory

**改动文件**: `src/graph/preprocessing.py`

在 `node_preprocess` 的 Path A 中，新增从 Redis 加载 L2a/L2b 的逻辑：

```python
# 新增 import
from src.memory.session_memory import get_session_memory

async def _run_normal_preprocessing(user_input: str, user_id: str, state: dict) -> dict:
    session_id = state.get("session_id", user_id)  # 需要在 state 中传递 session_id
    session_mem = get_session_memory(session_id)

    # 并行加载：intent + entity + memory + session_window + session_summary
    intent_task = classify_intent(user_input)
    entity_task = extract_entities(user_input)
    window_task = session_mem.get_window()      # L2a
    summary_task = session_mem.get_summary()    # L2b

    # ... recall 逻辑不变 ...
    intent_result, entities, memories, window, summary = await asyncio.gather(
        intent_task, entity_task, memory_task, window_task, summary_task
    )

    return {
        "intent": intent,
        "entities": entities,
        "memory_chunks": memories,
        "search_plan": search_plan,
        "session_window": window,        # 新增字段
        "session_summary": summary,      # 新增字段
    }
```

### 1.2 在 postprocessing 中写入 Session Memory

**改动文件**: `src/graph/postprocessing.py`

```python
from src.memory.session_memory import get_session_memory

async def node_postprocess(state: dict) -> dict:
    # ... 现有逻辑 ...

    # 写入 L2a 滑动窗口（Redis RPUSH，亚毫秒级）
    session_id = state.get("session_id", user_id)
    session_mem = get_session_memory(session_id)
    await session_mem.add_turn(user_input, response)

    # trim 必须异步解耦！trim() 内部调用 LLM 做摘要压缩（1~3s），
    # 如果同步 await 会导致主链路阻塞，前端卡在 loading。
    asyncio.create_task(session_mem.trim())

    # 原有的 L2c 向量记忆写入逻辑不变（也是 fire-and-forget）
    if should_save_memory(user_input, entities):
        asyncio.create_task(write_chunk(...))

    return {}
```

**面试要点**: `add_turn` 是 Redis RPUSH（亚毫秒），必须同步确保写入成功。
`trim` 涉及 LLM 压缩（1~3s），用 `create_task` 丢进后台，主链路立即返回。
这体现了"主链路只做极速写入，重计算异步解耦"的工程原则。

### 1.2.1 修复 trim() 的 Race Condition（关键）

**问题**: 现有 `session_memory.py:60-101` 的 trim 实现存在致命竞态：

```
第6轮 trim 触发 → LPOP 2条 → LLM 压缩中(1~3s) → 第7轮 add_turn RPUSH → 第8轮 add_turn RPUSH
                                                          ↑
                                              LLM 完成 → SET summary
                                              但 summary 只包含第1轮内容，
                                              第7/8轮的新消息在 List 里，与 summary 断层
```

更危险的是：如果 LPOP 之后、LLM 返回之前进程宕机，那 2 条消息**永久丢失**。

**解法: LRANGE 复制 + Lua 脚本原子裁剪**

改动文件: `src/memory/session_memory.py`

```python
# Redis Lua 脚本：原子化 "验证头部 → 裁剪 → 更新摘要"
# 如果 List 头部不是预期的消息（说明期间有其他操作），则放弃裁剪
_TRIM_LUA = """
local key = KEYS[1]
local summary_key = KEYS[2]
local evict_count = tonumber(ARGV[1])
local new_summary = ARGV[2]
local ttl = tonumber(ARGV[3])

-- 验证 List 头部是否是我们预期的那批消息
local head = redis.call('LRANGE', key, 0, evict_count - 1)
local expected = cmsgpack.unpack(ARGV[4])
for i = 1, evict_count do
    if head[i] ~= expected[i] then
        return 0  -- 头部已变，放弃裁剪
    end
end

-- 原子执行：裁剪 + 更新摘要
redis.call('LTRIM', key, evict_count, -1)
redis.call('SET', summary_key, new_summary, 'EX', ttl)
return 1  -- 成功
"""

class SessionMemory:
    """Per-session memory backed by Redis."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._redis = get_redis()
        self._trim_script = self._redis.register_script(_TRIM_LUA)

    async def add_turn(self, user_msg: str, assistant_msg: str) -> None:
        """Append a conversation turn (user + assistant) to the window."""
        pair = json.dumps({"user": user_msg, "assistant": assistant_msg}, ensure_ascii=False)
        await self._redis.rpush(self._msg_key(), pair)
        await self._redis.expire(self._msg_key(), MSG_TTL)
        logger.info("turn_added", session_id=self.session_id)

    async def get_window(self) -> list[dict]:
        """Get the recent sliding window messages as [{role, content}, ...]."""
        raw = await self._redis.lrange(self._msg_key(), 0, -1)
        messages = []
        for item in raw:
            pair = json.loads(item)
            messages.append({"role": "user", "content": pair["user"]})
            messages.append({"role": "assistant", "content": pair["assistant"]})
        return messages

    async def get_summary(self) -> str:
        """Get the compressed summary of older turns."""
        return await self._redis.get(self._summary_key()) or ""

    async def trim(self) -> str | None:
        """Trim messages beyond the sliding window, compress evicted turns.

        Race-condition safe:
        1. LRANGE 复制（不弹出）最老的 N 条消息
        2. LLM 异步压缩（1~3s，期间 List 不变，add_turn 可继续 RPUSH）
        3. Lua 脚本原子验证+裁剪：如果 List 头部仍是那 N 条，才 LPOP + SET summary

        Returns the updated summary, or None if no trimming needed.
        """
        msg_key = self._msg_key()
        summary_key = self._summary_key()
        count = await self._redis.llen(msg_key)
        max_messages = WINDOW_SIZE * 2

        if count <= max_messages:
            return None

        # Step 1: LRANGE 复制（不修改 List）
        evict_count = count - max_messages
        evicted_raw = await self._redis.lrange(msg_key, 0, evict_count - 1)
        if not evicted_raw:
            return None

        # Step 2: LLM 压缩（耗时操作，期间 List 可继续被 RPUSH）
        evicted = []
        for item in evicted_raw:
            pair = json.loads(item)
            evicted.append({"role": "user", "content": pair["user"]})
            evicted.append({"role": "assistant", "content": pair["assistant"]})

        existing_summary = await self.get_summary()
        to_compress = []
        if existing_summary:
            to_compress.append({"role": "system", "content": f"之前的对话摘要：{existing_summary}"})
        to_compress.extend(evicted)

        new_summary = await compress(to_compress)

        # Step 3: Lua 脚本原子裁剪
        import msgpack
        packed_heads = msgpack.packb(evicted_raw)
        result = await self._trim_script(
            keys=[msg_key, summary_key],
            args=[evict_count, new_summary, MSG_TTL, packed_heads],
        )

        if result == 1:
            logger.info("trimmed", session_id=self.session_id,
                         evicted=evict_count, summary_len=len(new_summary))
            return new_summary
        else:
            # 头部已变（期间有 LPOP 或其他操作），放弃本次裁剪
            # 下次 trim 调用时会重新计算
            logger.warning("trim_aborted", session_id=self.session_id,
                           reason="head_changed_during_compression")
            return None
```

**Lua 脚本原子性保证**:
```
验证头部 == 预期？──→ 是 → LTRIM + SET summary (原子)
                    └→ 否 → 放弃，返回 0
```

- `LRANGE` 复制不修改 List，即使 LLM 耗时 3 秒，add_turn 的 RPUSH 不受影响
- Lua 脚本在 Redis 单线程中原子执行，验证+裁剪之间不可能被插入其他命令
- 如果验证失败（头部被其他操作修改），安全放弃，下次重试

**面试话术**: "现有实现先 LPOP 再调 LLM，存在两个风险：LLM 期间新消息与 summary 断层，
以及进程宕机导致消息永久丢失。我用 LRANGE 复制 + Lua 脚本 CAS 解决：
复制不改 List，LLM 跑完后用 Lua 原子验证头部是否仍是那批消息，是才裁剪。"

**改动文件**: `src/graph/agent_state.py`

```python
class AgentState(TypedDict):
    # ... 现有字段 ...
    session_id: str                    # 新增：用于 Redis key
    session_window: list               # 新增：L2a 滑动窗口
    session_summary: str               # 新增：L2b 历史摘要
```

### 1.4 Context Assembler 接入真实数据

**改动文件**: `src/graph/react_node.py`（或 Agent 调用 LLM 的位置）

将 state 中的 `session_window`、`session_summary`、`memory_chunks`、`user_profile` 传入 `assemble()`：

```python
from src.memory.context_assembler import assemble

messages = await assemble(
    system_prompt=system_prompt,
    current_input=user_input,
    working_memory={...},
    retrieved_products=search_results,
    vector_memories=state.get("memory_chunks", []),
    window_messages=state.get("session_window", []),       # 之前传的是 None
    profile_summary=format_profile_summary(profile),
    historical_summary=state.get("session_summary", ""),    # 之前传的是 None
)
```

### 1.5 自定义 Redis Checkpointer（进阶，可选）

**新增文件**: `src/graph/redis_checkpointer.py`

继承 LangGraph 的 `BaseCheckpointSaver`，用 Redis 实现状态持久化：

```python
from langgraph.checkpoint.base import BaseCheckpointSaver

class RedisCheckpointer(BaseCheckpointSaver):
    """Redis-backed checkpointer for cross-session state persistence."""

    async def aput(self, config, checkpoint, metadata, new_versions):
        thread_id = config["configurable"]["thread_id"]
        key = f"checkpoint:{thread_id}"
        serialized = self.serde.dumps(checkpoint)
        await self.redis.set(key, serialized, ex=86400)  # 24h TTL

    async def aget(self, config):
        thread_id = config["configurable"]["thread_id"]
        key = f"checkpoint:{thread_id}"
        data = await self.redis.get(key)
        return self.serde.loads(data) if data else None

    async def aput_writes(self, config, writes, task_id):
        # 支持增量写入（用于 interrupt/resume 场景）
        ...
```

**注意**：此步骤涉及 LangGraph 内部 API，需确认当前版本的 `BaseCheckpointSaver` 接口。如果接口不稳定，可暂缓，Phase 1 先用 MemorySaver + Redis Session Memory 的组合。

### Phase 1 交付物
- [ ] 多轮对话中 Agent 能看到历史窗口和摘要
- [ ] postprocess 自动维护滑动窗口和压缩
- [ ] AgentState 新增 session 相关字段
- [ ] Context Assembler 的 P5/P7 不再为空

---

## Phase 2: 双路召回 + LLM 辅助偏好提取

### 2.0 修复 Qdrant Collection 架构（关键）

**问题**: 现有 `memory_retriever.py:25-45` 为每个用户创建独立 Collection：

```python
def _collection_name(user_id: str) -> str:
    safe = hashlib.md5(user_id.encode()).hexdigest()[:12]
    return f"memory_{safe}"  # memory_a1b2c3d4e5f6

async def _ensure_collection(user_id: str) -> str:
    col = _collection_name(user_id)
    store = get_vector_store()
    await store.create_collection(col, DIMENSIONS)  # 每用户一个 Collection
```

**致命问题**: Qdrant 每个 Collection 内部是一个独立的 RocksDB 实例 + HNSW 索引。
10 万用户 = 10 万个 RocksDB 实例 → 内核文件描述符耗尽 → OOM。
即使 Qdrant 官方声称支持"海量 Collection"，实际生产中千级就已经是上限。

**解法: 统一 Collection + user_id Payload 过滤**

改动文件: `src/memory/memory_retriever.py`

```python
# === 改动前（每用户一个 Collection）===
def _collection_name(user_id: str) -> str:
    safe = hashlib.md5(user_id.encode()).hexdigest()[:12]
    return f"memory_{safe}"

# === 改动后（统一 Collection）===
MEMORY_COLLECTION = "user_long_term_memories"  # 全局唯一

async def _ensure_collection() -> str:
    """确保统一 Collection 存在，带 user_id payload 索引。"""
    store = get_vector_store()
    try:
        await store.create_collection(MEMORY_COLLECTION, DIMENSIONS)
        # 为 user_id 建立 payload 索引（Qdrant 会对其做分区过滤优化）
        await store.create_payload_index(
            MEMORY_COLLECTION,
            field_name="user_id",
            field_type="keyword",  # 精确匹配，不需要向量索引
        )
        # 为 timestamp 建索引（用于定时清理的范围查询）
        await store.create_payload_index(
            MEMORY_COLLECTION,
            field_name="timestamp",
            field_type="float",
        )
    except Exception:
        pass  # Collection may already exist
    return MEMORY_COLLECTION


async def write_chunk(
    user_id: str,
    user_input: str,
    assistant_output: str,
    entities: dict | None = None,
    intent: str | None = None,
    category: str | None = None,
    importance: float = 1.0,
) -> None:
    """Write a dialog chunk to the unified memory collection."""
    col = await _ensure_collection()  # 不再传 user_id
    embedder = get_embedder()

    parts = [f"用户: {user_input}", f"助手: {assistant_output}"]
    if entities:
        ent_str = ", ".join(f"{k}={v}" for k, v in entities.items()
                           if v and k not in ("ambiguous", "ambiguous_fields"))
        if ent_str:
            parts.append(f"实体: {ent_str}")
    if intent:
        parts.append(f"意图: {intent}")
    if category:
        parts.append(f"品类: {category}")

    text = "\n".join(parts)
    vector = await embedder.aembed(text)

    payload = {
        "user_id": user_id,          # payload 索引字段
        "user_input": user_input,
        "assistant_output": assistant_output,
        "entities": entities or {},
        "intent": intent or "",
        "category": category or "",
        "timestamp": time.time(),
        "importance": importance,
        "text": text,
    }

    # chunk_id 包含 user_id，保证全局唯一且不冲突
    chunk_id = _make_chunk_id(user_id, time.time())
    store = get_vector_store()
    await store.upsert(col, [chunk_id], [vector], [payload])
    logger.info("chunk_written", user_id=user_id, category=category)


async def recall(
    user_id: str,
    query: str,
    category: str | None = None,
    top_k: int | None = None,
) -> list[dict]:
    """Recall from unified collection, filtered by user_id."""
    col = MEMORY_COLLECTION  # 直接用常量
    store = get_vector_store()
    embedder = get_embedder()

    k = top_k or RECALL_TOP_K
    query_vector = await embedder.aembed(query)

    # Payload 过滤：Qdrant 对 keyword 索引字段的过滤做了深度优化
    filters = {"must": [{"key": "user_id", "match": {"value": user_id}}]}
    if category:
        filters["must"].append({"key": "category", "match": {"value": category}})

    results = await store.search(col, query_vector, limit=k, filters=filters)
    # ... 后续处理不变 ...
```

**架构对比**:

| 维度 | 旧（每用户 Collection） | 新（统一 Collection） |
|------|----------------------|---------------------|
| Collection 数 | = 用户数（10万+） | 1 个 |
| RocksDB 实例 | 10 万个 | 1 个 |
| 内存句柄 | OOM 风险 | 固定 |
| HNSW 索引 | 每个 Collection 独立小索引 | 1 个大索引，分区过滤 |
| 查询性能 | 小 Collection 检索快但浪费资源 | Qdrant 对 payload filter 有深度优化 |
| 初始化 | 每次写入前 `_ensure_collection` | 启动时创建一次 |

**面试话术**: "原有设计每用户一个 Collection，小规模测试没问题，但上线后 10 万用户
= 10 万个 RocksDB 实例，内核 OOM。改为统一 Collection + user_id payload 索引，
Qdrant 对 keyword 字段的分区过滤做了深度优化，性能不降，资源可控。"

**改动文件**: `src/memory/memory_retriever.py`

将 `should_recall` 从纯触发词匹配升级为 **意图召回 + 语义召回** 双路并发：

```python
async def should_recall_dual(
    query: str,
    user_id: str,
    current_category: str | None = None,
    prev_category: str | None = None,
    semantic_threshold: float = 0.75,
) -> tuple[bool, str]:
    """双路召回判断：意图召回（显式）+ 语义召回（隐式）。

    Returns:
        (should_recall, reason): reason 为 "explicit" | "semantic" | "none"
    """
    # 路径 1: 意图召回 — 触发词 / 跨品类跳转（现有逻辑）
    for trigger in REFERENCE_TRIGGERS:
        if trigger in query:
            return True, "explicit"

    if current_category and prev_category and current_category != prev_category:
        return True, "explicit"

    # 路径 2: 语义召回 — 与历史记忆做相似度检索
    # 即使没有触发词，如果当前 query 与某条历史记忆高度相似，也召回
    embedder = get_embedder()
    query_vector = await embedder.aembed(query)
    store = get_vector_store()

    try:
        filters = {"must": [{"key": "user_id", "match": {"value": user_id}}]}
        results = await store.search(MEMORY_COLLECTION, query_vector, limit=1, filters=filters)
        if results and results[0].get("score", 0) >= semantic_threshold:
            return True, "semantic"
    except Exception:
        pass  # collection 可能不存在

    return False, "none"
```

**preprocessing 中的调用改为**:

```python
# 旧：should_recall(user_input, current_category=None, prev_category=prev_category)
# 新：
do_recall, recall_reason = await should_recall_dual(
    user_input, user_id,
    current_category=None,
    prev_category=prev_category,
)
if do_recall:
    memory_task = recall(user_id, user_id, user_input)
    logger.info("recall_triggered", reason=recall_reason)
else:
    memory_task = _empty_list()
```

### 2.2 LLM 辅助偏好分类（轻量前置过滤 + 批量触发）

**改动文件**: `src/graph/postprocessing.py`

**问题**: 导购对话细碎（"这个太贵了"、"换个绿色的"），如果每轮都调 LLM 判断偏好，
调用频率 = 对话轮数 = 100%。高并发下 Token 成本和 LLM 吞吐量是硬瓶颈。

**解法: 两级过滤架构**

```python
from src.models.llm_client import get_llm
from src.memory.session_memory import get_session_memory

# === 第一级: 轻量规则过滤（本地，零延迟）===

# 明确无偏好的"废话模式"，直接 skip
_NOISE_PATTERNS = [
    r"^(好的|嗯|哦|行|可以|谢谢|谢了|知道了)$",
    r"^(第[一二三]个|这个|那个|左边|右边)$",           # 纯指代，无偏好信号
    r"^帮我(看看|瞧瞧|搜搜|查查)",                      # 纯操作指令
]

def _is_noise(text: str) -> bool:
    """第一级过滤：明显的废话/纯操作，直接跳过。"""
    text = text.strip()
    return any(re.match(p, text) for p in _NOISE_PATTERNS)

# 明确包含偏好的"信号模式"，直接写入，不需要 LLM
_STRONG_SIGNAL_PATTERNS = [
    r"我一直", r"我是\w+皮", r"我偏好", r"我的肤质",
    r"我常用", r"我经常买", r"我不喜欢\w+牌",
    r"预算.{0,5}\d+", r"不要超过\d+", r"\d+以内",
]

def _has_strong_signal(text: str) -> bool:
    """第一级过滤：明确的偏好信号，直接写入，跳过 LLM。"""
    return any(re.search(p, text) for p in _STRONG_SIGNAL_PATTERNS)


# === 第二级: 批量 LLM 分类（异步，低频）===

_PREFERENCE_CLASSIFY_PROMPT = """分析以下 3 轮对话，提取用户的长期偏好。

长期偏好：身份特征、稳定习惯、持久偏好（肤质、常买品牌、价格敏感度）
临时需求：一次性购买请求、当下场景需求

对话记录：
{conversation}

输出 JSON: {{
  "preferences": [
    {{"text": "用户原文摘录", "is_long_term": bool, "preference_type": str, "confidence": float}}
  ]
}}
preference_type: "skin_type" | "brand_preference" | "price_sensitivity" | "style_preference" | "temporary" | "other"
"""

async def _batch_classify_preferences(session_id: str):
    """每 3 轮对话触发一次批量 LLM 偏好提取（后台任务）。"""
    session_mem = get_session_memory(session_id)
    window = await session_mem.get_window()

    if len(window) < 6:  # 至少 3 轮（6 条 message）
        return

    # 取最近 3 轮
    recent = window[-6:]
    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in recent)

    llm = get_llm()
    result = await llm.chat_json([
        {"role": "system", "content": "你是偏好分析助手。只输出 JSON。"},
        {"role": "user", "content": _PREFERENCE_CLASSIFY_PROMPT.format(
            conversation=conversation,
        )},
    ])

    # 将确认的长期偏好写入 L2c 和 L3
    for pref in result.get("preferences", []):
        if pref.get("is_long_term") and pref.get("confidence", 0) > 0.7:
            # 写入向量记忆
            await write_chunk(
                user_id=session_id,
                user_input=pref["text"],
                assistant_output="[从批量偏好分析中提取]",
                entities={},
                intent="preference",
                category=pref.get("category", ""),
            )
            logger.info("batch_preference_saved",
                       text=pref["text"][:50], type=pref.get("preference_type"))


# === 主链路 postprocess（极速路径）===

async def node_postprocess(state: dict) -> dict:
    user_input = _get_user_input(state)
    response = _get_final_response(state)
    entities = state.get("entities", {})
    user_id = state.get("user_id", "default_user")
    session_id = state.get("session_id", user_id)

    if not user_input or not response:
        return {}

    # ---- L2a: 写入滑动窗口（同步，Redis 极快）----
    session_mem = get_session_memory(session_id)
    await session_mem.add_turn(user_input, response)

    # ---- L2b: trim 异步（涉及 LLM 压缩）----
    asyncio.create_task(session_mem.trim())

    # ---- L2c: 向量记忆写入（两级过滤）----
    if _has_strong_signal(user_input):
        # 第一级: 强信号直接写入，跳过 LLM
        intent_raw = state.get("intent", {})
        intent_str = intent_raw.get("user_goal", "") if isinstance(intent_raw, dict) else str(intent_raw)
        asyncio.create_task(write_chunk(
            user_id=user_id, user_input=user_input, assistant_output=response,
            entities=entities, intent=intent_str, category=entities.get("category"),
        ))
        logger.info("memory_saved", reason="strong_signal")
    elif _is_noise(user_input):
        # 第一级: 废话直接跳过
        logger.info("memory_skipped", reason="noise")
    else:
        # 中间地带：不调 LLM，交给批量任务
        # 用 Redis 计数器，每 3 轮触发一次批量 LLM 分类
        counter_key = f"session:{session_id}:turn_count"
        from src.db.redis_client import get_redis
        redis = get_redis()
        count = await redis.incr(counter_key)
        await redis.expire(counter_key, 3600)
        if count % 3 == 0:
            # 分布式锁防并发：同一 session 同一时间只允许一个批量任务
            lock_key = f"lock:batch_memory:{session_id}"
            acquired = await redis.set(lock_key, "1", nx=True, ex=10)
            if acquired:
                asyncio.create_task(
                    _batch_classify_preferences_with_lock(session_id, lock_key)
                )
                logger.info("batch_triggered", turn_count=count)
            else:
                logger.info("batch_skipped", reason="lock_held", turn_count=count)
        else:
            logger.info("memory_deferred", turn_count=count)

    return {}
```

**并发竞态防护**:

```
第3轮: count=3, SETNX lock → 成功, 启动后台任务A
第4轮: count=4, 跳过
第5轮: count=5, 跳过
第6轮: count=6, SETNX lock → 失败(任务A还在跑), 跳过
第7轮: count=7, 跳过
第8轮: count=8, 跳过
第9轮: count=9, SETNX lock → 成功(任务A已完成,锁已过期), 启动后台任务B
```

```python
async def _batch_classify_preferences_with_lock(session_id: str, lock_key: str):
    """带锁的批量偏好分类，完成后释放锁。"""
    from src.db.redis_client import get_redis
    redis = get_redis()
    try:
        await _batch_classify_preferences(session_id)
    finally:
        # 释放锁（只释放自己持有的锁）
        await redis.delete(lock_key)
        logger.info("batch_lock_released", session_id=session_id)
```

**面试要点**:
- 第一级规则过滤（本地，零延迟）淘汰 60%+ 的无意义输入
- 强信号直接写入，废话直接跳过，中间地带交给批量任务
- LLM 调用频率从 100% 降到 ~33%（每 3 轮一次批量分类）
- SETNX 分布式锁防止同一 session 的并发批量任务重复写入
- 锁 TTL 10 秒兜底，即使任务异常未释放也会自动过期
- 主链路 postprocess 延迟 < 5ms（只有 Redis 写入 + create_task）

### 2.3 偏好自动写入 L3 Profile

**改动文件**: `src/memory/user_profile.py`（新增辅助函数）、`src/memory/behavior_tracker.py`

Profile 更新不在主链路执行，而是在 `_batch_classify_preferences` 后台任务中同步完成。
批量 LLM 分类结果同时驱动 L2c 写入和 L3 更新，一次后台任务完成两件事。

```python
# 在 _batch_classify_preferences 中追加 Profile 更新逻辑：

async def _batch_classify_preferences(session_id: str):
    # ... 现有的 LLM 分类逻辑 ...

    for pref in result.get("preferences", []):
        if pref.get("is_long_term") and pref.get("confidence", 0) > 0.7:
            # 写入 L2c 向量记忆
            await write_chunk(...)

            # 同步更新 L3 Profile
            await _update_profile_from_classification(session_id, pref)
            logger.info("batch_preference_saved",
                       text=pref["text"][:50], type=pref.get("preference_type"))


async def _update_profile_from_classification(user_id: str, classification: dict):
    """从批量 LLM 分类结果自动更新 User Profile。"""
    from src.memory.user_profile import update_profile, get_profile

    pref_type = classification.get("preference_type", "")
    text = classification.get("text", "")
    category = classification.get("category", "general")
    updates = {}

    if pref_type == "brand_preference":
        # 从分类文本中提取品牌（LLM 返回的 text 通常是用户原话）
        # 简单提取：用已有的 entity_extractor 或关键词匹配
        brand = _extract_brand_from_text(text)
        if brand:
            profile = get_profile(user_id, category)
            brands = profile.get("preferred_brands", [])
            if brand not in brands:
                brands.append(brand)
                updates["preferred_brands"] = brands[-5:]

    elif pref_type == "price_sensitivity":
        # 从分类文本中推断价格敏感度
        price_hint = _extract_price_from_text(text)
        if price_hint:
            if price_hint < 500:
                updates["price_sensitivity"] = 0.8
            elif price_hint > 3000:
                updates["price_sensitivity"] = 0.2

    elif pref_type == "skin_type":
        # 肤质信息直接写入
        profile = get_profile(user_id, category)
        profile["skin_type"] = text  # 简化处理
        updates["skin_type"] = text

    if updates:
        update_profile(user_id, category, updates)
```

### Phase 2 交付物
- [ ] 语义召回：连续讨论同类话题时自动捞出历史偏好
- [ ] 两级过滤：规则前置淘汰 60%+ 废话，LLM 批量分类频率降至 ~33%
- [ ] 主链路 postprocess 延迟 < 5ms（Redis 写入 + create_task）
- [ ] 偏好自动同步到 L3 User Profile

---

## Phase 3: 记忆遗忘与衰减机制

### 3.1 向量记忆时间衰减

**改动文件**: `src/memory/memory_retriever.py`

在 payload 中增加 `importance` 字段，在召回时应用衰减：

```python
import math

DECAY_LAMBDA = 0.001  # 衰减速率，可配置
MIN_SCORE_THRESHOLD = 0.3  # 低于此分数的记忆不返回

async def write_chunk(..., importance: float = 1.0):
    payload = {
        # ... 现有字段 ...
        "importance": importance,       # 新增：初始重要度
        "last_recalled": time.time(),   # 新增：上次被召回的时间
        "recall_count": 0,              # 新增：被召回次数
    }

async def recall(user_id, query, category=None, top_k=None):
    results = await store.search(col, query_vector, limit=k * 2, filters=filters)  # 多取一些

    now = time.time()
    memories = []
    for r in results:
        payload = r.get("payload", {})
        base_score = r.get("score", 0)
        importance = payload.get("importance", 1.0)
        timestamp = payload.get("timestamp", now)

        # 衰减公式：score × importance × e^(-λ × days_elapsed)
        days_elapsed = (now - timestamp) / 86400
        decayed_score = base_score * importance * math.exp(-DECAY_LAMBDA * days_elapsed)

        if decayed_score >= MIN_SCORE_THRESHOLD:
            memories.append({
                "text": payload.get("text", ""),
                "score": decayed_score,
                "original_score": base_score,
                "user_input": payload.get("user_input", ""),
                "assistant_output": payload.get("assistant_output", ""),
                "category": payload.get("category", ""),
                "days_old": round(days_elapsed, 1),
            })

    # 按衰减后分数排序，取 top_k
    memories.sort(key=lambda x: x["score"], reverse=True)
    return memories[:top_k or RECALL_TOP_K]
```

### 3.2 矛盾检测与记忆覆盖

**改动文件**: `src/memory/memory_retriever.py`

**核心原则: 永不修改已有记录。矛盾通过"新记录自然覆盖旧记录"解决。**

当用户说 "我以前喜欢苹果，现在换成华为了"：
- 旧记录 "喜欢苹果" 保留在 Qdrant 中（importance=1.0, timestamp=旧时间）
- 新记录 "换成华为" 写入（importance=1.0, timestamp=当前时间）
- 读取时，新记录 decayed_score 自然高于旧记录（更近期 × 相同 importance）
- 如果语义相似度足够高，两条记录都会被召回，LLM 自行判断新旧

**但存在边界情况**: 用户问 "我喜欢苹果吗？" → 旧记录语义匹配度更高，可能误导。
解决方案：矛盾检测不是为了修改旧记录，而是为了让新记录携带更丰富的上下文。

```python
async def write_chunk_with_contradiction_awareness(
    user_id, user_input, assistant_output, entities, ...
):
    """写入时检测矛盾，为新记录注入对比上下文。"""
    category = entities.get("category")
    contradiction_detected = False

    if category:
        existing = await recall(user_id, user_input, category=category, top_k=3)
        for mem in existing:
            if _is_contradictory(user_input, mem["user_input"], entities):
                contradiction_detected = True
                logger.info("memory_contradiction_detected",
                           old=mem["user_input"][:50], new=user_input[:50])
                break

    # 新记录写入时，如果检测到矛盾，在 text 中注入对比上下文
    # 这样召回时 LLM 能看到 "用户从 X 转向了 Y"
    if contradiction_detected:
        enhanced_input = f"[偏好变更] {user_input}（之前偏好: {mem['user_input'][:50]}）"
    else:
        enhanced_input = user_input

    await write_chunk(
        user_id=user_id,
        user_input=enhanced_input,
        assistant_output=assistant_output,
        entities=entities, ...
    )

def _is_contradictory(new_input: str, old_input: str, entities: dict) -> bool:
    """简单的矛盾检测：检查否定词 + 同实体。"""
    negation_words = ["不", "不要", "不用", "不喜欢", "换成", "改了", "现在"]
    has_negation = any(w in new_input for w in negation_words)
    return has_negation and any(
        kw in old_input for kw in entities.get("brand", "").split(",") if kw
    )
```

**面试要点**: "我不修改旧记录，因为 Qdrant 的强项是检索不是更新。
新记录天然更近期，衰减分更高。对于边界情况（用户问'我喜欢苹果吗'），
我在新记录中注入了对比上下文，让 LLM 看到完整的偏好变迁轨迹。
这比硬删旧记录更好 — 保留了用户的偏好演变历史。"

### 3.3 定时清理（只删不改）

**新增文件**: `src/memory/memory_decay.py`

**核心原则: 衰减在读取时动态计算，数据库永不动态更新。**

Qdrant 擅长向量检索，忌讳大规模高频 Payload 更新。
10 万用户 × 50 条记忆 = 500 万次 update_payloads → 索引频繁重建，CPU 飙升。

正确做法：
- **写入时**: Payload 带 `timestamp` 和 `importance`，写入后不再修改
- **读取时**: 在内存中计算 `score × importance × e^(-λt)`（见 3.1 的 recall 函数）
- **定时任务**: 只做一件事 — delete 掉 `当前时间 - timestamp > 180天` 的死记忆

```python
import time

MAX_AGE_DAYS = 180  # 超过 180 天的记忆直接删除

async def cleanup_expired_all():
    """清理所有用户的过期记忆 — 统一 Collection，按 timestamp 范围删除。"""
    store = get_vector_store()
    cutoff = time.time() - MAX_AGE_DAYS * 86400

    # 直接在统一 Collection 上按 timestamp 范围删除
    # 不需要遍历用户，Qdrant 的 payload 索引会高效定位
    await store.delete_by_filter(MEMORY_COLLECTION, filter={
        "must": [
            {"key": "timestamp", "range": {"lt": cutoff}},
        ]
    })
    logger.info("expired_memories_cleaned", cutoff_days=MAX_AGE_DAYS)

async def cleanup_expired_user(user_id: str):
    """清理单个用户的过期记忆（用于用户注销等场景）。"""
    store = get_vector_store()
    cutoff = time.time() - MAX_AGE_DAYS * 86400

    await store.delete_by_filter(MEMORY_COLLECTION, filter={
        "must": [
            {"key": "user_id", "match": {"value": user_id}},
            {"key": "timestamp", "range": {"lt": cutoff}},
        ]
    })
    logger.info("user_expired_cleaned", user_id=user_id, cutoff_days=MAX_AGE_DAYS)
```

**读写比优化**:
| 操作 | 频率 | 方式 |
|------|------|------|
| 写入 | 每轮对话 | upsert 1 条（带 timestamp + importance） |
| 读取 | 每次召回 | search + 内存中计算衰减分 |
| 更新 | **永远不** | payload 写入后不再修改 |
| 删除 | 每天/每周 | 只删超 180 天的死数据 |

**面试杀手锏**: "衰减是读取时动态发生的数学变换，不是写入时的物理操作。
数据库只负责存原始数据，计算层负责赋予时间语义。这是 CQRS 思想在记忆系统中的应用。"

### Phase 3 交付物
- [ ] 召回结果按 `score × importance × time_decay` 排序（读取时动态计算）
- [ ] 矛盾检测 + 新记录注入对比上下文（不修改旧记录）
- [ ] 定时任务只 delete 超 180 天的死记忆，不 update payload
- [ ] Qdrant 零写放大：payload 写入后永不修改，衰减是纯数学变换

---

## Phase 4: 隐式反馈闭环

### 4.1 用户行为信号采集

**改动文件**: `src/graph/postprocessing.py`、新增 `src/memory/behavior_tracker.py`

```python
# src/memory/behavior_tracker.py

from dataclasses import dataclass
from src.memory.user_profile import update_profile, get_profile

@dataclass
class BehaviorSignal:
    user_id: str
    category: str
    action: str           # "click" | "skip" | "select" | "reject" | "dwell"
    product_price: float | None = None
    product_brand: str | None = None
    duration_ms: int | None = None

async def process_signal(signal: BehaviorSignal):
    """从用户行为信号更新 Profile。"""
    profile = get_profile(signal.user_id, signal.category)
    updates = {}

    if signal.action == "select" and signal.product_price:
        # 用户选中了商品 → 收窄价格区间
        price_range = profile.get("price_range")
        if price_range:
            # 指数移动平均
            alpha = 0.3
            new_min = price_range[0] * (1 - alpha) + signal.product_price * alpha
            new_max = price_range[1] * (1 - alpha) + signal.product_price * alpha
            updates["price_range"] = (round(new_min), round(new_max))
        else:
            # 首次选择，以 ±30% 作为初始区间
            updates["price_range"] = (
                round(signal.product_price * 0.7),
                round(signal.product_price * 1.3),
            )

    elif signal.action == "reject" and signal.product_brand:
        # 用户拒绝了某品牌 → 加入 negative preference
        negative = profile.get("negative_brands", [])
        if signal.product_brand not in negative:
            negative.append(signal.product_brand)
            updates["negative_brands"] = negative[-3:]  # 最多保留 3 个

    elif signal.action == "click" and signal.product_brand:
        # 用户点击了某品牌 → 强化品牌偏好
        brands = profile.get("preferred_brands", [])
        if signal.product_brand not in brands:
            brands.append(signal.product_brand)
            updates["preferred_brands"] = brands[-5:]

    if updates:
        update_profile(signal.user_id, signal.category, updates)
```

### 4.2 前端事件上报

**改动文件**: `frontend/` 中的商品卡片组件

前端在用户交互时发送行为事件：

```typescript
// 商品卡片点击/选择/拒绝时上报
const reportBehavior = (action: string, product: Product) => {
  fetch('/api/behavior', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sessionId,
      action,  // "click" | "select" | "reject"
      product_price: product.price,
      product_brand: product.brand,
      category: product.category,
    }),
  });
};
```

### 4.3 行为上报 API

**改动文件**: `src/api/chat.py`（新增端点）

```python
@app.post("/api/behavior")
async def report_behavior(request: Request):
    body = await request.json()
    signal = BehaviorSignal(
        user_id=body["session_id"],
        category=body.get("category", ""),
        action=body["action"],
        product_price=body.get("product_price"),
        product_brand=body.get("product_brand"),
    )
    await process_signal(signal)
    return {"status": "ok"}
```

### Phase 4 交付物
- [ ] 用户点击/选择/拒绝商品后，L3 Profile 自动更新
- [ ] 价格区间通过 EMA 收窄
- [ ] 拒绝的品牌加入 negative preference
- [ ] 前端行为上报 API

---

## Context Assembler 冲突处理 — 分层陈述 + 时间标签

随着记忆层增多，潜在冲突增加。**核心原则：越具体、越近期的记忆权重越高；大盘画像做兜底润色。冲突不靠规则硬解，靠分层陈述抛给 LLM 的 Context 理解能力。**

| 层级 | 角色 | 解释权 | 注入位置 |
|------|------|--------|---------|
| L1/L2a（当前窗口） | 用户当下的纠偏 | **最高** — 代表最新意图 | P5: 作为 messages 交替注入 |
| L2c（向量事实） | 曾经发生的客观事实 | 中等 — 需配合时间判断 | P4: 附加时间标签 + 品类标签 |
| L3（Profile 画像） | 用户的稳定倾向 | **最低** — 作为底色 | P6: 作为 System Prompt 前缀 |

**具体改动**: 重构 `context_assembler.py` 的 System Prompt 拼装逻辑：

```python
async def assemble(...) -> list[dict]:
    # ...

    # === P6: L3 Profile 作为"用户底色"（System Prompt 前缀）===
    if profile_content:
        messages.append({"role": "system", "content":
            f"【用户底色】\n{profile_content}\n"
            f"以上是该用户的长期偏好画像，作为推荐的参考倾向，但不作为硬约束。"
        })

    # === P7: L2b 摘要作为"历史脉络" ===
    if summary_content:
        messages.append({"role": "system", "content":
            f"【历史脉络】\n{summary_content}"
        })

    # === P4: L2c 向量记忆作为"历史事实"（带时间标签）===
    if recall_content:
        recall_lines = []
        for m in vector_memories[:3]:
            days = m.get("days_old", 0)
            time_label = f"{days}天前" if days > 0 else "今天"
            cat_label = f"[{m['category']}]" if m.get("category") else ""
            recall_lines.append(f"  - [{time_label}]{cat_label} {m['text'][:200]}")
        recall_content = "\n".join(recall_lines)
        messages.append({"role": "system", "content":
            f"【历史事实】\n{recall_content}\n"
            f"以上是与当前话题相关的历史对话记录，请注意时间远近：越近期的事实参考价值越高。"
        })

    # === P5: L2a 窗口消息（最高优先级，直接交替注入）===
    messages.extend(window_messages)

    # === P1: 当前输入（始终最后）===
    messages.append({"role": "user", "content": current_input})

    return messages
```

**注入后的 System Prompt 效果示例**:

```
【用户底色】
[手机] 价格敏感度: 高，偏好品牌: 小米, Redmi，浏览次数: 12
以上是该用户的长期偏好画像，作为推荐的参考倾向，但不作为硬约束。

【历史脉络】
用户之前在看手机，预算 2000 左右，关注拍照和续航。

【历史事实】
  - [3天前][手机] 用户: 帮我看看小米的，助手: 推荐了 Redmi Note 13 Pro
  - [今天][数码] 用户: 有没有适合送礼的，助手: 推荐了 5000 元档的旗舰机
以上是与当前话题相关的历史对话记录，请注意时间远近：越近期的事实参考价值越高。

[用户最新消息] 有没有高端一点的，送长辈
```

**面试话术**: "我把冲突消解从代码规则转移到了 LLM 的 Context 理解能力上。
通过分层陈述（底色 → 事实 → 当前）和时间标签，让 LLM 自行判断：
'用户平时对价格敏感，但这次是送礼场景，应该推荐高端产品'。
这比硬编码优先级规则更灵活，也更符合真实导购场景。"

---

## 实施顺序与依赖关系

```
Phase 1 (接通 Session Memory)
  ├── 1.1 preprocessing 加载 L2a/L2b
  ├── 1.2 postprocessing 写入 L2a + trim (async)
  ├── 1.3 AgentState 扩展
  ├── 1.4 Context Assembler 接入真实数据
  └── 1.5 Redis Checkpointer (可选，不影响其他)

Phase 2 (双路召回 + 偏好提取)    ← 依赖 Phase 1
  ├── 2.1 双路召回策略
  ├── 2.2 两级过滤偏好分类（规则前置 + LLM 批量）
  └── 2.3 偏好自动写入 Profile

Phase 3 (遗忘机制)                ← 依赖 Phase 1（只需 timestamp 字段）
  ├── 3.1 读取时动态衰减（score × importance × e^(-λt)）
  ├── 3.2 矛盾检测
  └── 3.3 定时清理（只删 180 天以上死数据，不更新 payload）

Phase 4 (隐式反馈)                ← 独立，可与 Phase 2/3 并行
  ├── 4.1 行为信号处理
  ├── 4.2 前端事件上报
  └── 4.3 行为上报 API
```

Phase 2 和 Phase 3 可以并行开发：
- Phase 2 的两级过滤只需要现有的 `should_save_memory` 正则作为第一级
- Phase 3 的读取时衰减只需要 payload 中已有 `timestamp` 字段
- 两者唯一的交叉点是 `importance` 字段（Phase 2 的 LLM 分类结果会写入 importance），但 Phase 3 在 importance 缺失时默认 1.0，不影响独立交付

---

## 面试展示建议

每个 Phase 对应一个面试故事线：

| Phase | 面试关键词 | 故事线 |
|-------|-----------|--------|
| Phase 1 | **系统集成** | "我发现了实现与集成的断层，设计了节点注入法将 Redis 状态注入 LangGraph 工作流" |
| Phase 2 | **检索系统设计** | "我将正则匹配升级为双路并发召回，意图召回捕获显式引用，语义召回捕捉隐式关联" |
| Phase 3 | **系统成熟度** | "衰减是读取时的数学变换，不是写入时的物理操作 — CQRS 思想在记忆系统中的应用" |
| Phase 4 | **闭环推荐** | "我将 Agent 从聊天机器人升级为生成式推荐系统，通过隐式反馈实现偏好自迭代" |

**异步化是贯穿所有 Phase 的加分项**：

主链路只做 L1/L2a 的极速写入（Redis RPUSH < 1ms），L2c/L3 的复杂处理全部异步：

| 操作 | 执行方式 | 延迟 |
|------|---------|------|
| L2a add_turn | 同步 await | < 1ms |
| L2b trim（LLM 压缩） | `asyncio.create_task` | 后台 1~3s |
| L2c write_chunk（强信号） | `asyncio.create_task` | 后台 < 100ms |
| L2c 批量 LLM 分类 | 每 3 轮触发一次 | 后台 2~5s |
| L4 行为信号处理 | API 异步调用 | 后台 < 50ms |

**主链路 postprocess 总延迟 < 5ms**，用户无感知。
