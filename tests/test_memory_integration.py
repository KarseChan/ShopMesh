"""Memory System Integration Tests — Phase 1~4 end-to-end verification.

Covers:
  - Session Memory (L2a/L2b) write + read
  - Dual recall (explicit trigger + semantic similarity)
  - Two-level preference classification (noise/signal/batch)
  - L3 Profile load + write-back
  - Contradiction detection + context injection
  - Read-time decay
  - Behavior signal processing

All external services (Redis, Qdrant, LLM, Embedder) are faked in-memory.
No infrastructure required — runs with `pytest tests/test_memory_integration.py -v`.
"""

import json
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────
# Fake Redis — in-memory simulation
# ──────────────────────────────────────────────

class FakeRedis:
    """In-memory Redis simulation supporting list/string/counter/TTL operations."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}
        self._ttls: dict[str, float] = {}
        self._locks: dict[str, str] = {}

    # ── String ops ──

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx:
            if key in self._data or key in self._locks:
                return False
            self._locks[key] = value
        self._data[key] = value
        if ex:
            self._ttls[key] = time.time() + ex
        return True

    async def incr(self, key: str) -> int:
        val = int(self._data.get(key, "0")) + 1
        self._data[key] = str(val)
        return val

    async def expire(self, key: str, ttl: int) -> None:
        self._ttls[key] = time.time() + ttl

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)
            self._lists.pop(key, None)
            self._ttls.pop(key, None)
            self._locks.pop(key, None)

    # ── List ops ──

    async def rpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).append(value)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self._lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start:stop + 1]

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        lst = self._lists.get(key, [])
        if stop == -1:
            self._lists[key] = lst[start:]
        else:
            self._lists[key] = lst[start:stop + 1]

    # ── Lua eval (simplified: just execute ltrim + set) ──

    async def eval(self, script: str, numkeys: int, *args) -> int:
        # Simplified Lua: verify head matches, then ltrim + set summary
        msg_key = args[0]
        summary_key = args[1]
        evict_count = int(args[2])
        new_summary = args[3]
        ttl = int(args[4])
        expected_json = args[5]

        current_head = self._lists.get(msg_key, [])[:evict_count]
        expected = json.loads(expected_json)

        if len(current_head) != len(expected):
            return 0
        for i in range(len(current_head)):
            if current_head[i] != expected[i]:
                return 0

        # Atomic: trim + set summary
        self._lists[msg_key] = self._lists.get(msg_key, [])[evict_count:]
        self._data[summary_key] = new_summary
        if ttl:
            self._ttls[summary_key] = time.time() + ttl
        return 1


# ──────────────────────────────────────────────
# Fake Vector Store — in-memory simulation
# ──────────────────────────────────────────────

class FakeVectorStore:
    """In-memory Qdrant simulation with payload filtering and cosine similarity."""

    def __init__(self):
        self._collections: dict[str, list[dict]] = {}
        self._indexes: dict[str, set[str]] = {}  # collection → indexed fields

    async def create_collection(self, collection: str, dimension: int) -> None:
        if collection not in self._collections:
            self._collections[collection] = []

    async def create_payload_index(self, collection: str, field_name: str, field_type: str) -> None:
        self._indexes.setdefault(collection, set()).add(field_name)

    async def upsert(self, collection: str, ids: list[str],
                     vectors: list[list[float]], payloads: list[dict]) -> None:
        col = self._collections.setdefault(collection, [])
        for id_, vec, pl in zip(ids, vectors, payloads):
            # Upsert: remove existing with same id
            col[:] = [p for p in col if p["id"] != id_]
            col.append({"id": id_, "vector": vec, "payload": pl})

    async def search(self, collection: str, query_vector: list[float],
                     limit: int = 10, filters: dict | None = None) -> list[dict]:
        col = self._collections.get(collection, [])
        results = []
        for point in col:
            if filters and not self._match_filters(point["payload"], filters):
                continue
            score = self._cosine_similarity(query_vector, point["vector"])
            results.append({"id": point["id"], "score": score, "payload": point["payload"]})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    async def delete_by_filter(self, collection: str, filters: dict) -> None:
        col = self._collections.get(collection, [])
        remaining = []
        for point in col:
            if self._match_filters(point["payload"], filters):
                continue  # delete this point
            remaining.append(point)
        self._collections[collection] = remaining

    def _match_filters(self, payload: dict, filters: dict) -> bool:
        """Check if payload matches all filter conditions."""
        for key, value in filters.items():
            if isinstance(value, dict) and "range" in value:
                pv = payload.get(key, 0)
                range_spec = value["range"]
                if "lt" in range_spec and not (pv < range_spec["lt"]):
                    return False
                if "gt" in range_spec and not (pv > range_spec["gt"]):
                    return False
                if "lte" in range_spec and not (pv <= range_spec["lte"]):
                    return False
                if "gte" in range_spec and not (pv >= range_spec["gte"]):
                    return False
            else:
                if payload.get(key) != value:
                    return False
        return True

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def fake_vector_store():
    return FakeVectorStore()


@pytest.fixture
def fake_embedder():
    embedder = AsyncMock()
    embedder.aembed = AsyncMock(return_value=[1.0, 0.0, 0.0])
    return embedder


def _make_state(user_input: str, user_id: str = "test_user",
                session_id: str = "test_session", **kwargs) -> dict:
    """Build a minimal AgentState for testing."""
    state = {
        "messages": [{"role": "user", "content": user_input}],
        "user_id": user_id,
        "session_id": session_id,
        "final_response": kwargs.get("final_response", "推荐结果"),
        "entities": kwargs.get("entities", {}),
        "intent": kwargs.get("intent", {}),
        "session_window": [],
        "session_summary": "",
        "user_profile": {},
    }
    state.update(kwargs)
    return state


# ──────────────────────────────────────────────
# Test 1: Session Memory roundtrip
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_1_session_memory_roundtrip(fake_redis):
    """5 turns → Redis sliding window has 10 messages (5 user + 5 assistant)."""
    from src.memory.session_memory import SessionMemory

    with patch("src.memory.session_memory.get_redis", return_value=fake_redis):
        mem = SessionMemory("test_session")

        for i in range(5):
            await mem.add_turn(f"用户消息{i}", f"助手回复{i}")

        window = await mem.get_window()
        assert len(window) == 10  # 5 turns × 2 messages each
        assert window[0] == {"role": "user", "content": "用户消息0"}
        assert window[1] == {"role": "assistant", "content": "助手回复0"}
        assert window[9] == {"role": "assistant", "content": "助手回复4"}


# ──────────────────────────────────────────────
# Test 2: Noise skip
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_2_noise_skip(fake_redis):
    """'好的' → noise → skip, no L2c write, no L3 update."""
    from src.graph.postprocessing import node_postprocess

    state = _make_state("好的", final_response="推荐结果")

    with patch("src.graph.postprocessing.get_session_memory") as mock_sm, \
         patch("src.db.redis_client.get_redis", return_value=fake_redis), \
         patch("src.graph.postprocessing.write_chunk", new_callable=AsyncMock) as mock_wc, \
         patch("src.graph.postprocessing.write_chunk_with_contradiction_awareness",
               new_callable=AsyncMock) as mock_wc_contra, \
         patch("src.graph.postprocessing.update_profile_from_preference",
               new_callable=AsyncMock) as mock_up:

        mock_mem = AsyncMock()
        mock_mem.add_turn = AsyncMock()
        mock_mem.trim = AsyncMock()
        mock_sm.return_value = mock_mem

        result = await node_postprocess(state)

        # Noise → no L2c write
        mock_wc.assert_not_called()
        mock_wc_contra.assert_not_called()
        mock_up.assert_not_called()


# ──────────────────────────────────────────────
# Test 3: Strong signal write
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_3_strong_signal_write(fake_redis):
    """'我预算2000以内' → strong signal → write L2c + update L3."""
    from src.graph.postprocessing import node_postprocess

    state = _make_state(
        "我预算2000以内",
        entities={"category": "手机"},
        final_response="推荐结果",
    )

    with patch("src.graph.postprocessing.get_session_memory") as mock_sm, \
         patch("src.db.redis_client.get_redis", return_value=fake_redis), \
         patch("src.graph.postprocessing.write_chunk_with_contradiction_awareness",
               new_callable=AsyncMock) as mock_wc, \
         patch("src.graph.postprocessing.update_profile_from_preference",
               new_callable=AsyncMock) as mock_up:

        mock_mem = AsyncMock()
        mock_mem.add_turn = AsyncMock()
        mock_mem.trim = AsyncMock()
        mock_sm.return_value = mock_mem

        result = await node_postprocess(state)

        # Strong signal → L2c write triggered
        mock_wc.assert_called_once()
        call_kwargs = mock_wc.call_args
        assert call_kwargs.kwargs["user_id"] == "test_user" or call_kwargs[1]["user_id"] == "test_user"

        # Strong signal with "预算" → L3 price_sensitivity update
        mock_up.assert_called()
        up_calls = mock_up.call_args_list
        price_call = [c for c in up_calls if "price_sensitivity" in str(c)]
        assert len(price_call) > 0, "price_sensitivity update should be triggered"


# ──────────────────────────────────────────────
# Test 4: Dual recall — explicit trigger
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_4_dual_recall_explicit():
    """'我之前说过预算' → trigger word '之前' → recall triggered."""
    from src.memory.memory_retriever import should_recall_dual

    with patch("src.memory.memory_retriever.get_embedder") as mock_get_emb, \
         patch("src.memory.memory_retriever.get_vector_store") as mock_get_vs:

        mock_emb = AsyncMock()
        mock_emb.aembed = AsyncMock(return_value=[1.0, 0.0, 0.0])
        mock_get_emb.return_value = mock_emb

        mock_vs = FakeVectorStore()
        mock_get_vs.return_value = mock_vs

        should_recall, reason = await should_recall_dual(
            "我之前说过预算多少", "test_user",
        )

        assert should_recall is True
        assert reason == "explicit"


# ──────────────────────────────────────────────
# Test 5: Contradiction detection
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_5_contradiction_detection(fake_vector_store, fake_embedder):
    """Old memory '喜欢Nike' + new '换成华为的' → [偏好变更] prefix injected."""
    from src.memory.memory_retriever import (
        MEMORY_COLLECTION,
        _is_contradictory,
        write_chunk_with_contradiction_awareness,
    )

    # Test the predicate directly
    # Same brand + negation = contradiction
    assert _is_contradictory("我现在不喜欢Nike了", "我一直喜欢Nike", {"brand": "Nike"}) is True
    # Different brand, no overlap = not contradictory
    assert _is_contradictory("帮我看看手机", "我一直喜欢Nike", {"brand": "Nike"}) is False

    # Test the full write flow with existing memory
    # User previously liked Nike, now says they don't like Nike anymore
    now = time.time()
    fake_vector_store._collections[MEMORY_COLLECTION] = [{
        "id": "old_1",
        "vector": [1.0, 0.0, 0.0],
        "payload": {
            "user_id": "test_user",
            "user_input": "我一直喜欢Nike的鞋",
            "assistant_output": "推荐了Nike",
            "entities": {"brand": "Nike"},
            "intent": "search",
            "category": "手机",
            "timestamp": now - 86400,
            "importance": 1.0,
            "text": "用户: 我一直喜欢Nike的鞋\n助手: 推荐了Nike",
        },
    }]

    with patch("src.memory.memory_retriever.get_vector_store", return_value=fake_vector_store), \
         patch("src.memory.memory_retriever.get_embedder", return_value=fake_embedder):

        await write_chunk_with_contradiction_awareness(
            user_id="test_user",
            user_input="我现在不喜欢Nike了",
            assistant_output="好的，帮你排除Nike",
            entities={"brand": "Nike", "category": "手机"},
            intent="search",
            category="手机",
        )

    # Check that the new chunk has [偏好变更] prefix
    col = fake_vector_store._collections[MEMORY_COLLECTION]
    new_chunks = [p for p in col if "偏好变更" in p["payload"].get("user_input", "")]
    assert len(new_chunks) > 0, "New chunk should have [偏好变更] prefix"
    assert "之前偏好" in new_chunks[0]["payload"]["user_input"]


# ──────────────────────────────────────────────
# Test 6: Read-time decay
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_6_read_time_decay(fake_vector_store, fake_embedder):
    """Same importance, but old memory (30 days) scores lower than new memory."""
    from src.memory.memory_retriever import MEMORY_COLLECTION, recall

    now = time.time()

    # New memory (today)
    fake_vector_store._collections[MEMORY_COLLECTION] = [
        {
            "id": "new_memory",
            "vector": [1.0, 0.0, 0.0],
            "payload": {
                "user_id": "test_user",
                "user_input": "新记忆",
                "assistant_output": "新回复",
                "entities": {},
                "intent": "search",
                "category": "手机",
                "timestamp": now,
                "importance": 1.0,
                "text": "用户: 新记忆\n助手: 新回复",
            },
        },
        {
            "id": "old_memory",
            "vector": [0.99, 0.01, 0.0],  # slightly lower similarity
            "payload": {
                "user_id": "test_user",
                "user_input": "旧记忆",
                "assistant_output": "旧回复",
                "entities": {},
                "intent": "search",
                "category": "手机",
                "timestamp": now - 30 * 86400,  # 30 days ago
                "importance": 1.0,
                "text": "用户: 旧记忆\n助手: 旧回复",
            },
        },
    ]

    with patch("src.memory.memory_retriever.get_vector_store", return_value=fake_vector_store), \
         patch("src.memory.memory_retriever.get_embedder", return_value=fake_embedder):

        results = await recall("test_user", "测试查询", top_k=10)

    assert len(results) >= 2
    # New memory should rank higher due to recency decay
    assert results[0]["user_input"] == "新记忆"
    # Old memory should have lower score
    new_score = next(r["score"] for r in results if r["user_input"] == "新记忆")
    old_score = next(r["score"] for r in results if r["user_input"] == "旧记忆")
    assert new_score > old_score, f"New ({new_score}) should score higher than old ({old_score})"
    # Verify decay was applied
    assert results[0]["days_old"] < 1.0


# ──────────────────────────────────────────────
# Test 7: L3 Profile loaded in preprocessing
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_7_l3_profile_load_in_preprocessing(fake_redis, fake_vector_store, fake_embedder):
    """Preprocessing returns user_profile in state."""
    from src.graph.preprocessing import node_preprocess

    state = _make_state("帮我看看手机")

    with patch("src.graph.preprocessing.classify_intent", new_callable=AsyncMock) as mock_intent, \
         patch("src.graph.preprocessing.extract_entities", new_callable=AsyncMock) as mock_entity, \
         patch("src.graph.preprocessing.should_recall_dual", new_callable=AsyncMock) as mock_recall_dual, \
         patch("src.graph.preprocessing.recall", new_callable=AsyncMock) as mock_recall, \
         patch("src.graph.preprocessing.get_session_memory") as mock_sm, \
         patch("src.graph.preprocessing.get_global_profile") as mock_profile, \
         patch("src.graph.preprocessing.validate_entities", side_effect=lambda x: x), \
         patch("src.graph.preprocessing.normalize_soft_requirements", side_effect=lambda x: x), \
         patch("src.graph.preprocessing.plan_search", new_callable=AsyncMock) as mock_plan:

        mock_intent.return_value = ({"user_goal": "search", "task_type": "product"}, 0.9, "llm")
        mock_entity.return_value = {"category": "手机"}
        mock_recall_dual.return_value = (False, "none")
        mock_recall.return_value = []

        mock_mem = AsyncMock()
        mock_mem.get_window = AsyncMock(return_value=[])
        mock_mem.get_summary = AsyncMock(return_value="")
        mock_sm.return_value = mock_mem

        mock_profile.return_value = {"price_sensitivity": 0.5, "preferred_brands": [], "total_visits": 5}
        mock_plan.return_value = {"search_mode": "single", "search_requests": []}

        result = await node_preprocess(state)

        # Profile should be loaded
        assert "user_profile" in result
        assert result["user_profile"]["total_visits"] == 5
        mock_profile.assert_called_once_with("test_user")


# ──────────────────────────────────────────────
# Test 8: Behavior signal → Profile update
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_8_behavior_signal_update():
    """select + price=2000 + brand=小米 → price_range narrowed + brand added."""
    from src.memory.behavior_tracker import BehaviorSignal, process_signal
    from src.memory import user_profile

    # Reset profile cache
    user_profile._profiles.clear()
    user_profile._db_available = False

    with patch("src.memory.user_profile._try_db", return_value=False):
        signal = BehaviorSignal(
            user_id="test_behavior",
            category="手机",
            action="select",
            product_price=2000.0,
            product_brand="小米",
        )
        await process_signal(signal)

    profile = user_profile.get_profile("test_behavior", "手机")

    # Price range should be set (first selection: ±30%)
    assert profile["price_range"] is not None
    assert profile["price_range"][0] <= 2000 <= profile["price_range"][1]

    # Brand should be in preferred_brands
    assert "小米" in profile["preferred_brands"]

    # Cleanup
    user_profile._profiles.clear()


# ──────────────────────────────────────────────
# Test 9: Batch classify triggers at turn 3
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_9_batch_classify_triggers_at_turn_3(fake_redis):
    """3 non-noise turns → counter=3 → batch LLM triggered with SETNX lock."""
    from src.graph.postprocessing import node_postprocess

    mock_llm = AsyncMock()
    mock_llm.chat_json = AsyncMock(return_value={
        "preferences": [{
            "text": "用户喜欢小米品牌",
            "is_long_term": True,
            "preference_type": "brand_preference",
            "confidence": 0.9,
        }],
    })

    with patch("src.graph.postprocessing.get_session_memory") as mock_sm, \
         patch("src.db.redis_client.get_redis", return_value=fake_redis), \
         patch("src.graph.postprocessing.get_llm", return_value=mock_llm), \
         patch("src.graph.postprocessing.write_chunk", new_callable=AsyncMock), \
         patch("src.graph.postprocessing.write_chunk_with_contradiction_awareness",
               new_callable=AsyncMock), \
         patch("src.graph.postprocessing.update_profile_from_preference",
               new_callable=AsyncMock) as mock_up:

        mock_mem = AsyncMock()
        mock_mem.add_turn = AsyncMock()
        mock_mem.trim = AsyncMock()
        mock_mem.get_window = AsyncMock(return_value=[
            {"role": "user", "content": "帮我看看手机"},
            {"role": "assistant", "content": "推荐了小米"},
            {"role": "user", "content": "有没有性价比高的"},
            {"role": "assistant", "content": "推荐了Redmi"},
            {"role": "user", "content": "这个品牌怎么样"},
            {"role": "assistant", "content": "小米口碑不错"},
        ])
        mock_sm.return_value = mock_mem

        # Turn 1: non-noise, non-signal → counter becomes 1
        state1 = _make_state("有没有小米的手机", final_response="推荐结果")
        await node_postprocess(state1)

        # Turn 2: non-noise, non-signal → counter becomes 2
        state2 = _make_state("这个品牌的口碑怎么样", final_response="推荐结果")
        await node_postprocess(state2)

        # Turn 3: non-noise, non-signal → counter becomes 3 → batch triggered
        state3 = _make_state("续航能力好不好", final_response="推荐结果")
        await node_postprocess(state3)

        # Verify counter reached 3
        counter_val = await fake_redis.get(f"session:test_session:turn_count")
        assert int(counter_val) == 3

        # Verify LLM was called (batch classify)
        # Note: batch runs as asyncio.create_task, need to await it
        # Since create_task runs in the event loop, we give it a moment
        import asyncio
        await asyncio.sleep(0.1)

        # The LLM should have been called
        mock_llm.chat_json.assert_called()


# ──────────────────────────────────────────────
# Test 10: Full pipeline — preprocessing + postprocessing
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_10_full_pipeline(fake_redis, fake_vector_store, fake_embedder):
    """Full pipeline: preprocess → postprocess, verify data flows through."""
    from src.graph.preprocessing import node_preprocess
    from src.graph.postprocessing import node_postprocess

    # --- Preprocessing ---
    pre_state = _make_state("帮我看看2000以内的手机")

    with patch("src.graph.preprocessing.classify_intent", new_callable=AsyncMock) as mock_intent, \
         patch("src.graph.preprocessing.extract_entities", new_callable=AsyncMock) as mock_entity, \
         patch("src.graph.preprocessing.should_recall_dual", new_callable=AsyncMock) as mock_recall_dual, \
         patch("src.graph.preprocessing.recall", new_callable=AsyncMock), \
         patch("src.graph.preprocessing.get_session_memory") as mock_sm, \
         patch("src.graph.preprocessing.get_global_profile") as mock_profile, \
         patch("src.graph.preprocessing.validate_entities", side_effect=lambda x: x), \
         patch("src.graph.preprocessing.normalize_soft_requirements", side_effect=lambda x: x), \
         patch("src.graph.preprocessing.plan_search", new_callable=AsyncMock) as mock_plan:

        mock_intent.return_value = ({"user_goal": "search", "task_type": "product"}, 0.9, "llm")
        mock_entity.return_value = {"category": "手机", "price_max": 2000}
        mock_recall_dual.return_value = (False, "none")

        mock_mem = AsyncMock()
        mock_mem.get_window = AsyncMock(return_value=[])
        mock_mem.get_summary = AsyncMock(return_value="")
        mock_sm.return_value = mock_mem

        mock_profile.return_value = {"price_sensitivity": 0.7, "preferred_brands": [], "total_visits": 3}
        mock_plan.return_value = {"search_mode": "single", "search_requests": []}

        pre_result = await node_preprocess(pre_state)

    # Verify preprocessing outputs
    assert pre_result["user_profile"]["price_sensitivity"] == 0.7
    assert pre_result["entities"]["category"] == "手机"
    assert pre_result["session_window"] == []

    # --- Postprocessing ---
    post_state = {
        **pre_state,
        "entities": pre_result["entities"],
        "intent": pre_result["intent"],
        "final_response": "推荐了 Redmi Note 13 Pro",
    }

    with patch("src.graph.postprocessing.get_session_memory") as mock_sm_post, \
         patch("src.db.redis_client.get_redis", return_value=fake_redis), \
         patch("src.graph.postprocessing.write_chunk_with_contradiction_awareness",
               new_callable=AsyncMock) as mock_wc, \
         patch("src.graph.postprocessing.update_profile_from_preference",
               new_callable=AsyncMock):

        mock_mem_post = AsyncMock()
        mock_mem_post.add_turn = AsyncMock()
        mock_mem_post.trim = AsyncMock()
        mock_sm_post.return_value = mock_mem_post

        await node_postprocess(post_state)

        # Verify session memory was written
        mock_mem_post.add_turn.assert_called_once()
        call_args = mock_mem_post.add_turn.call_args
        assert "帮我看看2000以内的手机" in str(call_args)

        # Verify trim was scheduled
        mock_mem_post.trim.assert_called_once()
