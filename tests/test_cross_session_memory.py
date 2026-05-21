"""跨会话记忆排除测试 — 验证品牌偏好跨 session 生效。

测试流程:
  1. Session A: "给我推荐双肩包，我不喜欢小米的" → 写入 L2c 记忆 + L3 画像
  2. 等待异步写入完成
  3. Session B: "给我推荐双肩包，尽量便宜的" → 应召回记忆，排除小米

检查点:
  - postprocessing: strong signal 匹配 + 记忆写入
  - preprocessing: should_recall_dual 触发 + 记忆召回
  - react_node: 记忆注入 system prompt
  - 最终结果: 无小米商品

用法:
  python -m pytest tests/test_cross_session_memory.py -v -s
  # 或直接运行
  python tests/test_cross_session_memory.py
"""

import asyncio
import json
import time
import httpx

API_URL = "http://localhost:8000/api/chat"
USER_ID = "test_cross_session_001"
SESSION_A = "sess_cross_test_A"
SESSION_B = "sess_cross_test_B"


async def send_request(message: str, session_id: str, user_id: str) -> dict:
    """发送请求并收集所有 SSE 事件。"""
    events = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            API_URL,
            json={
                "message": message,
                "session_id": session_id,
                "user_id": user_id,
                "mode": "multi_agent",
            },
            headers={"Content-Type": "application/json"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = {"raw": data_str}
                    events.append({"event": event_type, "data": data})
    return events


def extract_products(events: list) -> list:
    """从 SSE 事件中提取商品列表。"""
    for e in events:
        if e["event"] == "results":
            return e["data"].get("products", [])
    return []


def extract_explanation(events: list) -> str:
    """从 SSE 事件中提取最终回复文本。"""
    for e in events:
        if e["event"] == "explanation":
            return e["data"].get("text", "")
    return ""


def contains_xiaomi(text: str) -> bool:
    """检查文本是否包含小米相关内容。"""
    xiaomi_keywords = ["小米", "Xiaomi", "xiaomi", "米家", "MI", "prod_051"]
    return any(kw in text for kw in xiaomi_keywords)


async def test_cross_session_memory():
    """跨会话记忆排除端到端测试。"""

    print("=" * 60)
    print("跨会话记忆排除测试")
    print("=" * 60)
    print(f"user_id: {USER_ID}")
    print(f"Session A: {SESSION_A}")
    print(f"Session B: {SESSION_B}")
    print()

    # ---- Step 1: Session A — 声明不喜欢小米 ----
    print("[Step 1] Session A: '给我推荐双肩包，我不喜欢小米的'")
    print("-" * 40)
    events_a = await send_request(
        "给我推荐双肩包，我不喜欢小米的",
        SESSION_A,
        USER_ID,
    )

    products_a = extract_products(events_a)
    explanation_a = extract_explanation(events_a)

    print(f"  事件数: {len(events_a)}")
    print(f"  返回商品数: {len(products_a)}")
    for p in products_a[:5]:
        name = p.get("name", "?")
        pid = p.get("product_id", "?")
        brand = p.get("brand", "?")
        price = p.get("price", "?")
        print(f"    - [{pid}] {name} (品牌: {brand}, ¥{price})")

    xiaomi_in_a = [p for p in products_a if contains_xiaomi(json.dumps(p, ensure_ascii=False))]
    print(f"  小米商品数: {len(xiaomi_in_a)}")
    print(f"  回复包含小米: {contains_xiaomi(explanation_a)}")
    print()

    # ---- 等待异步写入完成 ----
    print("[等待] 异步记忆写入 (3秒)...")
    await asyncio.sleep(3)
    print()

    # ---- Step 2: Session B — 只说尽量便宜 ----
    print("[Step 2] Session B: '给我推荐双肩包，尽量便宜的'")
    print("-" * 40)
    events_b = await send_request(
        "给我推荐双肩包，尽量便宜的",
        SESSION_B,
        USER_ID,
    )

    products_b = extract_products(events_b)
    explanation_b = extract_explanation(events_b)

    print(f"  事件数: {len(events_b)}")
    print(f"  返回商品数: {len(products_b)}")
    for p in products_b[:5]:
        name = p.get("name", "?")
        pid = p.get("product_id", "?")
        brand = p.get("brand", "?")
        price = p.get("price", "?")
        print(f"    - [{pid}] {name} (品牌: {brand}, ¥{price})")

    xiaomi_in_b = [p for p in products_b if contains_xiaomi(json.dumps(p, ensure_ascii=False))]
    print(f"  小米商品数: {len(xiaomi_in_b)}")
    print(f"  回复包含小米: {contains_xiaomi(explanation_b)}")
    print()

    # ---- Step 3: 判定 ----
    print("=" * 60)
    print("测试结果")
    print("=" * 60)

    # 检查 Session A 是否正确排除了小米
    if xiaomi_in_a:
        print(f"  [WARN] Session A 返回了 {len(xiaomi_in_a)} 个小米商品")
        for p in xiaomi_in_a:
            print(f"         - {p.get('name', '?')} ({p.get('product_id', '?')})")
    else:
        print("  [OK] Session A 无小米商品")

    # 核心检查: Session B 是否排除了小米
    if xiaomi_in_b:
        print(f"  [FAIL] Session B 返回了 {len(xiaomi_in_b)} 个小米商品 — 跨会话记忆排除失败!")
        for p in xiaomi_in_b:
            print(f"         - {p.get('name', '?')} ({p.get('product_id', '?')})")
    else:
        print("  [OK] Session B 无小米商品 — 跨会话记忆排除成功!")

    # 检查回复文本
    if contains_xiaomi(explanation_b):
        print("  [WARN] Session B 回复文本中提到了小米")

    print()

    # ---- Step 4: 日志诊断 ----
    print("[诊断] 检查 app.jsonl 日志...")
    print("-" * 40)
    try:
        with open("logs/app.jsonl", "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 查找与测试相关的日志
        test_logs = []
        for line in lines:
            try:
                entry = json.loads(line)
                event = entry.get("event", "")
                # 查找关键事件
                if any(kw in event for kw in [
                    "memory_saved", "memory_skipped", "memory_deferred",
                    "batch_triggered", "recall_triggered", "recall",
                    "chunk_written", "profile_updated",
                    "preprocess_done", "memory_contradiction",
                ]):
                    # 检查是否与测试用户相关
                    log_str = json.dumps(entry, ensure_ascii=False)
                    if USER_ID in log_str or "cross_test" in log_str or "default_user" in log_str:
                        test_logs.append(entry)
            except json.JSONDecodeError:
                continue

        # 只显示最近的 20 条相关日志
        for log in test_logs[-20:]:
            event = log.get("event", "")
            # 提取关键字段
            keys_to_show = {k: v for k, v in log.items()
                           if k not in ("event", "timestamp", "logger")
                           and v is not None and v != ""}
            print(f"  {event}: {json.dumps(keys_to_show, ensure_ascii=False)[:120]}")

        if not test_logs:
            print("  未找到相关日志 (可能 user_id 不匹配或日志已轮转)")

    except FileNotFoundError:
        print("  日志文件不存在: logs/app.jsonl")
    except Exception as e:
        print(f"  读取日志失败: {e}")

    print()

    # ---- 最终判定 ----
    passed = len(xiaomi_in_b) == 0
    if passed:
        print("RESULT: PASS — 跨会话品牌排除正常工作")
    else:
        print("RESULT: FAIL — Session B 未排除小米品牌")
        print()
        print("可能原因:")
        print("  1. postprocessing: '不喜欢小米的' 未匹配 strong signal → 记忆未写入")
        print("  2. preprocessing: should_recall_dual 未触发 (语义阈值 0.75 太高)")
        print("  3. L3 画像: preferred_brands 存了 '不喜欢小米的' (负面偏好当正面存)")
        print("  4. Agent: 记忆/画像注入了但 LLM 未正确解读排除意图")

    return passed


if __name__ == "__main__":
    result = asyncio.run(test_cross_session_memory())
    exit(0 if result else 1)
