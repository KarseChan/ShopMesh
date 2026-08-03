"""回归测试 — 推理模型 <think> 剥离。

背景:换用 MiniMax-M* 等推理模型后,思维链以 <think>...</think> **内联在
content**(无单独 reasoning 字段),而 chat_json/react 解析直接从 content 找
JSON。不剥离会把思维链当答案解析 → 实体抽取/工具选择全崩。

_strip_think 在 llm_client.chat 层集中剥离,对所有非流式调用(含 chat_json)生效。
"""

from src.models.llm_client import _strip_think


class TestStripThink:
    def test_complete_block_removed(self):
        assert _strip_think('<think>推理</think>\n\n{"intent":"search"}') == '{"intent":"search"}'

    def test_block_then_text(self):
        assert _strip_think('<think>a</think>Hello') == 'Hello'

    def test_no_think_passthrough(self):
        assert _strip_think('直接回答，没有思维链') == '直接回答，没有思维链'

    def test_json_fence_passthrough(self):
        s = '```json\n{"x":1}\n```'
        assert _strip_think(s) == s

    def test_truncated_unclosed_think_becomes_empty(self):
        # max_tokens 截断在思考中途 → 全部丢弃,调用方拿到空并显式失败,
        # 而不是把半截思维链当答案。
        assert _strip_think('<think>思考被截断没有闭合标签') == ''

    def test_non_string_passthrough(self):
        # tool_call 无 content 时为 None,应原样返回
        assert _strip_think(None) is None

    def test_multiple_blocks(self):
        assert _strip_think('<think>one</think>A<think>two</think>B') == 'AB'
