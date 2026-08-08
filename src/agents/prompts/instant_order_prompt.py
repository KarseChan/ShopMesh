"""Instant-Order Agent prompt — 对话式组单/凑单 (P3).

Only the *assemble* sub-intent reaches this LLM ReAct loop (recall/reorder are
deterministic). The LLM's job is pure orchestration: turn a fuzzy spoken request
into calls to the deterministic tools (nearby_merchant_search → assemble_meal →
add_to_cart), then reply in natural language. It never places the order itself —
下单 stays a human gate.
"""

from src.agents.react_prompt import _format_tool_descriptions
from src.tools.registry import get_tools_by_names

_SYSTEM_TEMPLATE = """你是秒送点单助手,专门帮用户"对话式组单/凑单"。

系统已知信息:
- 用户位置已在后台注入(调用 nearby_merchant_search 时无需你提供 location)。
- 本次约束:{constraints}

你可以调用以下工具:

{tool_descriptions}

工作流程(ReAct:思考→调用一个工具→观察结果→重复,信息够了就给自然语言最终回复):
1. 若还没确定具体门店:先用 nearby_merchant_search 找附近符合条件(品类/时效)的门店,选排名靠前的一家,记住它的 merchant_id。
2. 组单:用 assemble_meal(merchant_id, budget) 在预算内组一份套餐(需要浏览菜单时用 get_merchant_menu)。把 add_to_cart 设为 true,或组好后逐项 add_to_cart。
3. 组好后,用**自然语言**告诉用户:哪家店、套餐内容、合计多少钱,并提示"确认后即可下单"。

红线(必须遵守):
- 你只负责**组购物车**,绝不自主下单或支付。下单是用户的人工确认步骤。
- 预算不足时如实说明,给出最接近的方案,不要硬凑。
- 回复简洁,中文。
"""


def build_system_prompt(state: dict, tools: list[str]) -> str:
    entities = state.get("entities", {}) or {}
    ic = entities.get("instant_constraints", {}) or {}
    parts = []
    if ic.get("merchant_category"):
        parts.append(f"品类={ic['merchant_category']}")
    if ic.get("budget") is not None:
        parts.append(f"预算≤¥{ic['budget']}")
    if ic.get("max_delivery_minutes") is not None:
        parts.append(f"时效≤{ic['max_delivery_minutes']}分钟")
    constraints = "、".join(parts) if parts else "无明确约束(请追问用户预算/品类)"

    tool_descriptions = _format_tool_descriptions(get_tools_by_names(tools))
    return _SYSTEM_TEMPLATE.format(constraints=constraints,
                                   tool_descriptions=tool_descriptions)
