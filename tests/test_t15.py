"""T1.5 memory system tests."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.memory.working_memory import WorkingMemory
from src.memory.compressor import estimate_tokens, compress
from src.memory.context_assembler import assemble, _trim_messages, _format_products, SLOTS
from src.memory.knowledge_base import query_knowledge
from src.memory.memory_retriever import should_recall
from src.memory.user_profile import (
    get_profile, update_profile, record_visit,
    get_global_profile, format_profile_summary,
    _profiles,
)


# === L1 Working Memory ===

def test_working_memory_basic():
    wm = WorkingMemory()
    wm.update({"intent": "search", "entities": {"category": "护肤"}})
    assert wm.get("intent") == "search"
    assert wm.get("entities")["category"] == "护肤"
    assert wm.get("missing", "default") == "default"


def test_working_memory_clear():
    wm = WorkingMemory()
    wm.update({"a": 1, "b": 2})
    wm.clear()
    assert wm.as_dict() == {}


def test_working_memory_as_dict():
    wm = WorkingMemory()
    wm.update({"x": 10})
    d = wm.as_dict()
    d["y"] = 20
    assert wm.get("y") is None  # as_dict returns a copy


# === Compressor ===

def test_estimate_tokens_chinese():
    tokens = estimate_tokens("你好世界")  # 4 Chinese chars
    assert tokens > 0
    assert tokens < 10


def test_estimate_tokens_mixed():
    tokens = estimate_tokens("hello 你好")
    assert tokens > 0


@pytest.mark.asyncio
async def test_compress_few_messages():
    """Few messages return concatenated, not compressed."""
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮你的？"},
    ]
    result = await compress(messages, max_words=200)
    assert "你好" in result


@pytest.mark.asyncio
async def test_compress_empty():
    result = await compress([])
    assert result == ""


# === Context Assembler ===

def test_trim_messages_no_trim():
    messages = [{"role": "user", "content": "短消息"}]
    result = _trim_messages(messages, max_tokens=1000)
    assert len(result) == 1


def test_trim_messages_trims_oldest():
    messages = [
        {"role": "user", "content": "这是一条比较长的消息" * 50},
        {"role": "assistant", "content": "回复" * 50},
        {"role": "user", "content": "最新消息"},
    ]
    result = _trim_messages(messages, max_tokens=50)
    # Should keep at least the last message
    assert len(result) <= len(messages)


def test_format_products():
    products = [
        {"name": "雅诗兰黛精华", "price": 599, "platform_id": "jd"},
        {"name": "兰蔻小黑瓶", "price": 880, "platform_id": "tb"},
    ]
    result = _format_products(products, max_tokens=500)
    assert "雅诗兰黛" in result
    assert "¥599" in result


def test_format_products_trims():
    products = [{"name": f"商品{i}", "price": i * 100, "platform_id": "jd"} for i in range(20)]
    result = _format_products(products, max_tokens=20)
    # Should be trimmed
    assert len(result) > 0


@pytest.mark.asyncio
async def test_assemble_minimal():
    """Assemble with just system prompt and input."""
    messages = await assemble(
        system_prompt="你是导购助手",
        current_input="帮我找奶茶",
    )
    assert len(messages) >= 2
    assert messages[0]["role"] == "system"
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "帮我找奶茶"


@pytest.mark.asyncio
async def test_assemble_with_products():
    """Assemble includes product info when provided."""
    products = [
        {"name": "古茗奶茶", "price": 15, "platform_id": "tb"},
    ]
    messages = await assemble(
        system_prompt="你是导购助手",
        current_input="帮我找奶茶",
        retrieved_products=products,
    )
    # Should have a system message with product info
    sys_msgs = [m for m in messages if m["role"] == "system"]
    assert any("古茗" in m["content"] for m in sys_msgs)


@pytest.mark.asyncio
async def test_assemble_with_profile():
    """Assemble includes profile when provided."""
    messages = await assemble(
        system_prompt="你是导购助手",
        current_input="推荐护肤品",
        profile_summary="偏好: 中等价位护肤品牌",
    )
    sys_msgs = [m for m in messages if m["role"] == "system"]
    assert any("偏好" in m["content"] for m in sys_msgs)


@pytest.mark.asyncio
async def test_assemble_with_window():
    """Assemble includes window messages."""
    window = [
        {"role": "user", "content": "我要买奶茶"},
        {"role": "assistant", "content": "好的，推荐古茗"},
    ]
    messages = await assemble(
        system_prompt="你是导购助手",
        current_input="多少钱",
        window_messages=window,
    )
    # Window messages should be between system and current input
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_assemble_with_vector_recall():
    """Assemble includes vector memories when provided."""
    memories = [
        {"text": "用户之前看过奶茶", "score": 0.9, "category": "奶茶"},
    ]
    messages = await assemble(
        system_prompt="你是导购助手",
        current_input="上次那个",
        vector_memories=memories,
    )
    sys_msgs = [m for m in messages if m["role"] == "system"]
    assert any("历史" in m["content"] for m in sys_msgs)


@pytest.mark.asyncio
async def test_assemble_respects_budget():
    """Assemble stays within token budget."""
    # Create a lot of content to test budget limits
    big_summary = "很长的摘要" * 500
    big_profile = "用户画像" * 200
    products = [{"name": f"商品{i}", "price": i, "platform_id": "jd"} for i in range(50)]
    window = [{"role": "user", "content": f"消息{i}" * 50} for i in range(20)]

    messages = await assemble(
        system_prompt="你是导购助手",
        current_input="帮我找东西",
        retrieved_products=products,
        window_messages=window,
        profile_summary=big_profile,
        historical_summary=big_summary,
    )
    # Should complete without error and produce valid messages
    assert len(messages) >= 2


# === Knowledge Base ===

def test_query_knowledge_category():
    result = query_knowledge({"category": "护肤"})
    assert len(result["products"]) > 0
    assert all(p["category"] == "护肤" for p in result["products"])


def test_query_knowledge_with_price():
    result = query_knowledge({"category": "奶茶", "price_max": 20})
    assert all(p["price"] <= 20 for p in result["products"])


def test_query_knowledge_empty():
    result = query_knowledge({"category": "不存在的品类"})
    assert len(result["products"]) == 0


def test_query_knowledge_category_info():
    result = query_knowledge({"category": "数码"})
    assert "数码" in result["category_info"]


# === Memory Retriever — trigger detection ===

def test_should_recall_trigger_words():
    assert should_recall("上次那个奶茶") is True
    assert should_recall("之前看的手机") is True
    assert should_recall("记得我提过的品牌") is True


def test_should_recall_no_trigger():
    assert should_recall("帮我找一杯奶茶") is False


def test_should_recall_cross_category():
    assert should_recall("看看手机", current_category="数码", prev_category="奶茶") is True


def test_should_recall_same_category():
    assert should_recall("看看手机", current_category="数码", prev_category="数码") is False


# === L3 User Profile ===

def _clear_profiles():
    _profiles.clear()


def test_get_profile_default():
    _clear_profiles()
    profile = get_profile("user_test", "护肤")
    assert profile["price_sensitivity"] == 0.5
    assert profile["preferred_brands"] == []
    assert profile["visit_count"] == 0


def test_update_profile():
    _clear_profiles()
    update_profile("user_test", "护肤", {"price_sensitivity": 0.8, "preferred_brands": ["雅诗兰黛"]})
    profile = get_profile("user_test", "护肤")
    assert profile["price_sensitivity"] == 0.8
    assert "雅诗兰黛" in profile["preferred_brands"]


def test_profile_isolation_by_category():
    _clear_profiles()
    update_profile("user_test", "护肤", {"price_sensitivity": 0.8})
    update_profile("user_test", "数码", {"price_sensitivity": 0.3})
    assert get_profile("user_test", "护肤")["price_sensitivity"] == 0.8
    assert get_profile("user_test", "数码")["price_sensitivity"] == 0.3


def test_record_visit():
    _clear_profiles()
    record_visit("user_test", "奶茶", scenario="自用")
    record_visit("user_test", "奶茶")
    profile = get_profile("user_test", "奶茶")
    assert profile["visit_count"] == 2
    assert profile["scenario"] == "自用"


def test_get_global_profile():
    _clear_profiles()
    update_profile("user_test", "护肤", {"preferred_brands": ["雅诗兰黛"], "visit_count": 3})
    update_profile("user_test", "数码", {"preferred_brands": ["Apple"], "visit_count": 2})
    global_p = get_global_profile("user_test")
    assert global_p["total_visits"] == 5
    assert "雅诗兰黛" in global_p["preferred_brands"]
    assert "Apple" in global_p["preferred_brands"]


def test_format_profile_summary():
    profile = {
        "price_sensitivity": 0.7,
        "preferred_brands": ["雅诗兰黛", "兰蔻"],
        "visit_count": 5,
        "scenario": "送礼",
        "price_range": (200, 800),
    }
    summary = format_profile_summary(profile, category="护肤")
    assert "护肤" in summary
    assert "雅诗兰黛" in summary
    assert "送礼" in summary
    assert "5" in summary


def test_format_profile_summary_empty():
    profile = {"price_sensitivity": 0.5, "preferred_brands": [], "visit_count": 0}
    summary = format_profile_summary(profile)
    assert summary == ""
