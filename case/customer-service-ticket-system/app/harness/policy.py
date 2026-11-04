"""客服策略：澄清/结束/人工处理建议出口、生成上下文与提示词组装。

从 v1 引擎抽取的业务出口逻辑（共享 Harness 设计说明 第 3.2 节）。Harness 只负责
运行机制；"查什么、何时澄清、如何回答"由本策略决定。

出口话术约束（第 5.1 节）：
- 澄清、查询无结果如实表述；
- 人工处理建议只能表述"建议联系人工客服"等实际可证明的行为，
  未实现工单转交工具时，不声称已经创建记录或转交成功。
- 决策文本不直接作为最终答复流出。
"""
from __future__ import annotations

import json

from .contracts import Message

# 主动澄清：请求补充订单号或物流单号（与模糊工单 expected_behavior「提供订单号」对齐）
CLARIFY_TEXT = "请问您方便提供订单号或物流单号吗？这样我能帮您准确查询进展。"

# 人工处理建议：仅建议行为，不声称已转交或已创建记录
HANDOFF_TEXT = (
    "非常抱歉给您带来不好的体验。建议您联系人工客服进一步处理，"
    "我们会重视您反馈的问题。"
)

# 查询无结果：如实致歉，不虚构业务事实
NOT_FOUND_TEXT = (
    "抱歉，暂时没有查询到相关信息。请确认订单号是否正确，"
    "或联系人工客服获取进一步帮助。"
)

# 答复为空的兜底（计入 empty_answer 失败，不以模板伪装成功）
GEN_EMPTY_FALLBACK = "抱歉，我暂时无法回答这个问题。"

_INTENT_KEYWORDS = (
    ("退货", "return_policy"),
    ("退款", "refund_status"),
    ("换货", "exchange_policy"),
    ("投诉", "complaint"),
    ("电话", "contact_info"),
    ("400", "contact_info"),
)


def detect_intent_hint(query: str) -> str:
    """按关键词推断业务意图（用于 trace 展示，与 v1 口径一致）。"""
    for kw, intent in _INTENT_KEYWORDS:
        if kw in query:
            return intent
    return "logistics_query"


def aggregate_observations(observations: list[dict]) -> dict:
    """把工具最终观察（记录器 dict 结构）聚合为答复生成的参考数据。

    结构：{"order": <query_order 观察>, "logistics": <query_logistics 观察>,
          "knowledge": <search_knowledge 观察>}；未执行的键为 None。
    """
    agg: dict = {"order": None, "logistics": None, "knowledge": None}
    for obs in observations:
        if obs.get("rejected") or obs.get("error"):
            continue  # 出错/被拒的观察不进入答复依据，避免以错误结果伪装事实
        name = obs.get("tool")
        data = obs.get("data") or {}
        if name == "query_order":
            agg["order"] = data
        elif name == "query_logistics":
            agg["logistics"] = data
        elif name == "search_knowledge":
            agg["knowledge"] = data
    return agg


def has_found(agg: dict) -> bool:
    """聚合数据中是否存在任一 found=true 的有效结果。"""
    for key in ("order", "logistics", "knowledge"):
        wrapper = agg.get(key) or {}
        if wrapper.get("found"):
            return True
    return False


def build_generation_messages(
    system_prompt: str, query: str, agg: dict, response_gen_text: str
) -> list[Message]:
    """答复生成消息：系统提示 + 聚合工具观察 + 答复生成指令。

    聚合观察放在 tool 角色消息（内容为 JSON）；Provider 负责按各自协议转换。
    """
    order_info = json.dumps(agg.get("order") or {}, ensure_ascii=False)
    logistics_info = json.dumps(agg.get("logistics") or {}, ensure_ascii=False)
    gen_prompt = (
        response_gen_text
        .replace("{user_query}", query)
        .replace("{order_info}", order_info)
        .replace("{logistics_info}", logistics_info)
    )
    return [
        Message(role="system", content=system_prompt),
        Message(
            role="tool",
            content=json.dumps(
                {
                    "order": agg.get("order"),
                    "logistics": agg.get("logistics"),
                    "knowledge": agg.get("knowledge"),
                },
                ensure_ascii=False,
            ),
        ),
        Message(role="user", content=gen_prompt),
    ]
