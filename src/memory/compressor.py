"""Dialog Compressor — LLM-based summary of conversation history."""

from src.models.llm_client import get_llm
from src.observability.logger import get_logger

logger = get_logger("compressor")


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~2 Chinese chars per token, ~4 English chars per token."""
    cn_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other = len(text) - cn_chars
    return cn_chars // 2 + other // 4 + 1


async def compress(messages: list[dict], max_words: int = 200) -> str:
    """Compress a list of messages into a concise summary.

    Args:
        messages: List of {"role": "user"|"assistant", "content": str}
        max_words: Target summary length in Chinese characters

    Returns:
        Summary text (≤ max_words Chinese characters)
    """
    if not messages:
        return ""

    if len(messages) <= 2:
        # Too few messages to compress meaningfully
        return "\n".join(f"{m['role']}: {m['content']}" for m in messages)

    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in messages)

    prompt = (
        f"请将以下对话压缩为不超过{max_words}字的摘要，保留：\n"
        "1. 用户的核心需求和偏好（品牌、价格区间、品类）\n"
        "2. 已确认的关键决策\n"
        "3. 重要的实体信息（商品名、价格）\n"
        "丢弃：寒暄、重复信息、中间推理过程。\n\n"
        f"对话内容：\n{conversation}"
    )

    llm = get_llm()
    result = await llm.chat_json([
        {"role": "system", "content": "你是对话摘要助手。输出 JSON: {\"summary\": \"摘要文本\"}"},
        {"role": "user", "content": prompt},
    ])

    summary = result.get("summary", "")
    if not summary:
        # Fallback: just concatenate last few messages
        recent = messages[-3:]
        summary = "\n".join(f"{m['role']}: {m['content']}" for m in recent)

    logger.info("compressed", input_msgs=len(messages), summary_len=len(summary))
    return summary
