"""P0-3 回归测试 — 报错去"死胡同"化。

锁住:上下文过长时,先 reactive_compact,再 hard_truncate(只留 system + 最后一条
用户消息)续跑,而不是让用户"重新开始对话"。硬截断是纯函数,确定性可测。
"""

from src.graph.specialized_agents import _hard_truncate


class TestHardTruncate:
    def test_keeps_system_and_last_user(self):
        msgs = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "第一轮工具结果很长" * 100},
            {"role": "assistant", "content": "回复2"},
            {"role": "user", "content": "最后一句"},
        ]
        out = _hard_truncate(msgs)
        assert out[0]["content"] == "SYS", "必须保留 system prompt"
        assert out[-1]["content"] == "最后一句", "必须保留最后一条用户消息"
        assert len(out) == 2, "只留 system + 最后一条用户消息"

    def test_no_user_message_keeps_system_only(self):
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "assistant", "content": "只有助手"}]
        out = _hard_truncate(msgs)
        assert len(out) == 1 and out[0]["content"] == "SYS"

    def test_strictly_shrinks(self):
        msgs = [{"role": "system", "content": "S"}] + \
               [{"role": "user", "content": f"m{i}"} for i in range(20)]
        out = _hard_truncate(msgs)
        assert len(out) < len(msgs)
        assert out[-1]["content"] == "m19"
