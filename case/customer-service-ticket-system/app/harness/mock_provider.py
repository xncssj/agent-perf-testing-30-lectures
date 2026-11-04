"""MockProvider：无密钥、确定性、可复现的模拟模型（v2 Provider 契约）。

与 v1 MockLlm 的决策规则和答复生成保持一致，保证共享工单集上的
业务答复可对照回归：
- 通过规则识别意图、按意图规划工具调用序列；
- 参数抽不出来时退回问询澄清（clarify 动作）；
- 答复生成解析聚合工具观察，产出携带结构化事实的自然语言答复。

随机状态按运行隔离：每次运行使用独立 RNG（同 seed 各运行序列一致），
并发下不因共享随机数消费顺序而失去可复现性（共享 Harness 设计说明 第 6.1 节）。
延迟与 token 用量均为模拟数据（usage_source=estimated）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import AsyncIterator

from .budget import DeadlineExceeded
from .tokenization import token_count, tokenize
from .contracts import (
    AnswerChunk,
    Decision,
    Message,
    ModelUsage,
    RunContext,
    ToolCall,
)
from .providers import BaseProvider

ORDER_ID_RE = re.compile(r"(?<!\d)(\d{13})(?!\d)")
TRACKING_NO_RE = re.compile(r"(?P<carrier>SF|YT|ZTO|JD|EMS)\d{6,}")

# 金额诉求信号（performance-v1.1 规则用；单位：订单应付总额的中文问法）
AMOUNT_RE = re.compile(r"金额|多少钱|花费|费用|价格")
# 金额之外的物流/时效诉求信号：金额与物流诉求同现即 compound 族黄金用例
_LOGISTICS_SIGNALS = (
    "什么时候到", "什么时候能到", "多久到", "多久能到", "预计什么时候",
    "何时送达", "几天能到", "什么时候送到", "到哪了", "到哪", "物流",
    "配送", "快递", "运单", "送到了吗", "派送",
)
# 金额诉求不改变意图的 policy/转人工类意图（knowledge 或 handoff 路径）
_NON_DATA_INTENTS = (
    "refund_status", "complaint", "return_policy", "exchange_policy",
    "contact_info", "clarification",
)

# 订单状态 -> 答复中的中文表述（与共享数据集 test-data/generate.py 的 STATUS_ZH 保持一致）
STATUS_ZH = {"pending": "待发货", "shipped": "已发货", "exception": "异常"}

# 意图 -> 工具调用序列。顺序即模型规划的工具执行顺序。
_PLAN: dict[str, list[str]] = {
    "logistics_query": ["query_order", "query_logistics"],
    "delivery_time": ["query_order", "query_logistics"],
    "order_info": ["query_order"],
    "refund_status": ["query_order"],
    "return_policy": ["search_knowledge"],
    "exchange_policy": ["search_knowledge"],
    "contact_info": ["search_knowledge"],
    "complaint": [],
    "clarification": [],
}


# search_knowledge 检索词抽取词表（legacy 冻结口径；顺序即匹配优先级）
_KNOWLEDGE_KEYWORDS: dict[str, list[str]] = {
    "退货": ["退货"],
    "退款": ["退款"],
    "换货": ["换货"],
    "电话": ["客服电话"],
    "400": ["客服电话"],
}
# v1.1 词表：政策类复合词优先。单词“退货”检索的 top 命中全是 FAQ/商品文档，
# “售后”类预期词只在 policies:售后服务政策 标题中不可达（战役 A7）；
# “退货政策”检索可把政策文档带入命中（实测 top2），由 v1.1 答复聚合标题。
_KNOWLEDGE_KEYWORDS_V11: dict[str, list[str]] = {
    "退货政策": ["退货政策"],
    "退款政策": ["退款政策"],
    **_KNOWLEDGE_KEYWORDS,
}


def knowledge_query_of(query: str, v11: bool = False) -> str:
    """从工单文本抽取 search_knowledge 检索词。

    legacy 与 v1.1 共用词表结构；v1.1 前置政策类复合词（最长匹配优先），
    未命中时同样回退原文前 12 字。legacy 抽取结果与冻结基线逐字一致。
    """
    keywords = _KNOWLEDGE_KEYWORDS_V11 if v11 else _KNOWLEDGE_KEYWORDS
    for k, v in keywords.items():
        if k in query:
            return v[0]
    return query[:12]


class IntentDetector:
    """基于规则的意图识别，对齐 07-testing-scenarios 的业务意图集合。

    v1 等价说明：v1 决策在“tool_selection 提示词”上做识别，提示词内嵌工具
    描述（含“物流”字样），未命中前置关键词的查询一律落入 logistics_query。
    为保持 M0 冻结基线可对照（相同工单、相同工具序列），本检测器保留该
    等价缺省分支，不再返回 order_info / clarification。
    """

    def detect(self, query: str) -> str:
        q = query.lower()
        if re.search(r"退款.*(到哪|进度|状态|到账)", q) or re.search(
            r"(退款).*(什么时候)", q
        ):
            return "refund_status"
        if any(k in q for k in ["投诉", "投诉问题"]):
            return "complaint"
        if "换货" in q:
            return "exchange_policy"
        if "退货" in q or "退款" in q:
            return "return_policy"
        if any(k in q for k in ["电话", "400", "联系方式", "联系客服"]):
            return "contact_info"
        if any(
            k in q
            for k in ["什么时候到", "多久到", "多久能到", "预计什么时候", "何时送达", "几天能到"]
        ):
            return "delivery_time"
        if any(k in q for k in ["到哪了", "到哪", "物流", "配送", "快递", "运单", "送到了吗"]):
            return "logistics_query"
        # v1 等价缺省分支（见类 docstring）
        return "logistics_query"

    def detect_v1_1(self, query: str) -> str:
        """performance-v1.1 规则的意图识别（legacy 行为不走此分支）。

        在 legacy 判定之上恢复两类确定性路径：
        - 仅金额诉求（无物流/时效信号）→ order_info，工具序列只含 query_order，
          答复走订单模板携带金额，与黄金用例 expected_tools/expected_facts 一致；
        - 金额 + 物流/时效诉求 → 保留物流/时效意图（工具序列不变），
          金额事实由 v1.1 答复聚合承载（见 MockProvider._answer_v1_1）。
        政策/退款/转人工类意图不受金额词影响，避免误改知识路径。
        """
        q = query.lower()
        base = self.detect(q)
        if not AMOUNT_RE.search(q) or base in _NON_DATA_INTENTS:
            return base
        if any(k in q for k in _LOGISTICS_SIGNALS):
            return base
        return "order_info"


class MockProvider(BaseProvider):
    """确定性模拟模型。"""

    evidence_kind = "mock"  # 模拟数据证据类型（进入 metrics.evidence_kind）

    def __init__(self, settings) -> None:
        self.settings = settings
        self.model = settings.model_name
        self._seed = settings.mock_seed
        # Mock 规则版本：缺省 legacy 保持冻结基线行为；stub 配置对象缺字段时同样回落
        self.mock_rule_version = getattr(settings, "mock_rule_version", "legacy") or "legacy"
        self._v11 = self.mock_rule_version == "performance-v1.1"
        self.intent = IntentDetector()
        self._rngs: dict[str, random.Random] = {}

    def _rng(self, ctx: RunContext) -> random.Random:
        rng = self._rngs.get(ctx.run_id)
        if rng is None:
            rng = random.Random(self._seed)
            self._rngs[ctx.run_id] = rng
        return rng

    def _latency_ms(self, ctx: RunContext) -> float:
        """决策/答复延迟：基础值 + 可复现抖动。"""
        rng = self._rng(ctx)
        jitter = rng.uniform(
            -self.settings.mock_plan_jitter_ms, self.settings.mock_plan_jitter_ms
        )
        return max(10.0, self.settings.mock_plan_latency_ms + jitter)

    # ---- 决策 ----
    async def decide(
        self,
        messages: list[Message],
        tools: dict[str, dict],
        context: RunContext,
    ) -> Decision:
        t0 = time.perf_counter()
        query = _query_of(messages)
        used = _used_tools(messages)
        intent = self._detect(query)
        plan = self._plan_for(query, intent)
        remaining = [t for t in plan if t not in used]

        decision = Decision(action="finalize", model=self.model)
        if intent == "complaint":
            # 投诉意图直接转人工处理建议出口（与 v1 complaint 动作一致）
            decision.action = "handoff"
        elif remaining:
            tool = remaining[0]
            args = self._take_tool_args(query, tool)
            # 查询类工具参数缺失（订单号与运单号均未抽出）时退回问询澄清
            if tool in ("query_order", "query_logistics") and not (
                args.get("order_id") or args.get("tracking_no")
            ):
                decision.action = "clarify"
            else:
                decision.action = "call_tools"
                decision.tool_calls = [ToolCall(name=tool, arguments=args)]
        # 估算 token：中文按“字符数 / 2”近似
        decision.usage = ModelUsage(
            prompt_tokens=max(1, sum(len(m.content) for m in messages) // 2),
            completion_tokens=8,
            total_tokens=max(1, sum(len(m.content) for m in messages) // 2) + 8,
            source="estimated",
        )
        await self._sleep(self._latency_ms(context) / 1000.0, context)
        decision.latency_ms = (time.perf_counter() - t0) * 1000.0
        decision.ttft_ms = decision.latency_ms
        return decision

    # ---- 答复生成 ----
    async def stream_answer(
        self,
        messages: list[Message],
        context: RunContext,
    ) -> AsyncIterator[AnswerChunk]:
        t0 = time.perf_counter()
        if self._v11:
            answer = self._answer_v1_1(messages)
        else:
            answer = self._answer(messages)
        prompt_text = " ".join(m.content for m in messages)
        # 用量改用确定性 token 边界计数（替代此前的字符数估算），
        # 使 Mock 口径的 prompt/completion token 可复算（待办 2a）
        prompt_tokens = max(1, token_count(prompt_text))
        completion_tokens = max(1, token_count(answer))
        await self._sleep(self._latency_ms(context) / 1000.0, context)
        # 首 token 消耗生成延迟，其后按 token 间隔逐个产出：
        # 一个流式片段 = 一个 token（边界见 harness/tokenization.py），
        # 因此采集到的相邻片段间隔即 token 间隔，可用于 TPOT/ITL（待办 2a）
        token_gap = self.settings.mock_token_latency_ms / 1000.0
        for token in tokenize(answer):
            await self._sleep(token_gap, context)
            yield AnswerChunk(content=token)
        usage = ModelUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            source="estimated",
        )
        _ = t0
        yield AnswerChunk(content="", usage=usage, finish=True)

    def _detect(self, query: str) -> str:
        """按规则版本选择意图识别（legacy 与 performance-v1.1 各自确定性）。"""
        if self._v11:
            return self.intent.detect_v1_1(query)
        return self.intent.detect(query)

    def _answer(self, messages: list[Message]) -> str:
        """答复生成调用的确定性输出（legacy 规则，与 v1 冻结基线一致）。"""
        order, logistics, knowledge_hits = _observations(messages, last_only=True)
        return _render_answer(order, logistics, knowledge_hits)

    def _answer_v1_1(self, messages: list[Message]) -> str:
        """performance-v1.1 答复：聚合全部工具观察，金额与物流事实同现。

        legacy 只读最后一条工具观察，复合诉求下订单金额会被物流观察覆盖，
        compound 族黄金用例的 total_amount 事实因此恒不可达（战役 A7）；
        v1.1 聚合订单与物流观察，在不删除既有事实断言的前提下使其可达。
        知识答复同时携带命中文档标题：部分预期关键词（如 AP-POL-06 的
        “售后”）只出现在文档标题中，仅输出正文会使其确定不可达（战役 A7）。
        """
        order, logistics, knowledge_hits = _observations(messages, last_only=False)
        return _render_answer(order, logistics, knowledge_hits,
                              amount=True, doc_title=True)

    # ---- 计划逻辑（与 v1 MockLlm 一致）----
    def _plan_for(self, query: str, intent: str | None = None) -> list[str]:
        if intent is None:
            intent = self._detect(query)
        plan = list(_PLAN.get(intent, []))
        has_order = bool(ORDER_ID_RE.search(query))
        has_tracking = bool(TRACKING_NO_RE.search(query))
        if intent in ("logistics_query", "delivery_time") and not (has_order or has_tracking):
            # 既无订单号也无运单号：无法查询物流，跳过 query_logistics
            plan = [p for p in plan if p != "query_logistics"]
        if intent in ("logistics_query", "delivery_time") and has_tracking and not has_order:
            # 仅凭运单号：直接查物流（物流快照内含订单号），跳过 query_order
            plan = [p for p in plan if p != "query_order"]
        return plan

    def _take_tool_args(self, query: str, tool: str) -> dict:
        """按工具从工单文本中抽取参数。"""
        if tool == "query_order":
            m = ORDER_ID_RE.search(query)
            return {"order_id": m.group(1)} if m else {"order_id": ""}
        if tool == "query_logistics":
            m = TRACKING_NO_RE.search(query)
            if m:
                return {"tracking_no": m.group(0)}
            # 无运单号时回退用订单号查物流（物流工具支持按订单号查询）
            m = ORDER_ID_RE.search(query)
            return {"order_id": m.group(1)} if m else {"order_id": ""}
        if tool == "search_knowledge":
            # v1.1 政策类复合词优先（检索词抽取属 Mock 决策；检索器与词表
            # 结构不变，legacy 抽取结果与冻结基线一致）
            return {"query": knowledge_query_of(query, self._v11)}
        return {}

    async def _sleep(self, seconds: float, context: RunContext) -> None:
        """模拟延迟：整体 deadline 优先于模拟等待。"""
        remaining = context.remaining_s()
        if remaining <= 0:
            raise DeadlineExceeded()
        await asyncio.sleep(min(seconds, remaining))

    async def aclose(self) -> None:
        self._rngs.clear()


def _query_of(messages: list[Message]) -> str:
    for m in messages:
        if m.role == "user":
            return m.content
    return ""


def _used_tools(messages: list[Message]) -> list[str]:
    """历史中已出现过的工具名（含出错尝试，与 v1 口径一致）。"""
    used = []
    for m in messages:
        for call in m.tool_calls or []:
            if call.name not in used:
                used.append(call.name)
    return used


def _observations(messages: list[Message], last_only: bool):
    """解析工具观察为订单/物流/知识三类数据。

    last_only=True 复刻 legacy 口径（只读最后一条工具观察）；
    last_only=False 按时间顺序聚合全部工具观察，供 v1.1 复合诉求答复使用。
    """
    tool_messages = [m for m in messages if m.role == "tool"]
    if last_only and tool_messages:
        tool_messages = tool_messages[-1:]
    order: dict = {}
    logistics: dict = {}
    knowledge_hits: list = []
    for m in tool_messages:
        try:
            data = json.loads(m.content)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # 工具结果是 {"found": bool, ...} 包装结构，先解包再使用
        order_wrapper = data.get("order") or {}
        logistics_wrapper = data.get("logistics") or {}
        knowledge_wrapper = data.get("knowledge") or {}
        if order_wrapper.get("found") and order_wrapper.get("order"):
            order = order_wrapper["order"]
        if logistics_wrapper.get("found") and logistics_wrapper.get("logistics"):
            logistics = logistics_wrapper["logistics"]
        if knowledge_wrapper.get("found") and knowledge_wrapper.get("hits"):
            knowledge_hits = knowledge_wrapper["hits"]
    return order, logistics, knowledge_hits


def _render_answer(
    order: dict,
    logistics: dict,
    knowledge_hits: list,
    amount: bool = False,
    doc_title: bool = False,
) -> str:
    """按观察数据渲染确定性答复。

    amount=True 时订单与物流事实同现（v1.1）；doc_title=True 时知识答复
    携带命中文档标题（v1.1），legacy 两种口径都保持冻结基线模板。
    """
    if order.get("status") == "pending" and not logistics:
        if amount and order.get("total_amount"):
            return (
                f"您的订单 {order.get('order_id', '')} 当前状态为待发货，"
                f"订单金额合计 {order.get('total_amount', '')} 元。"
                "商品发货后物流信息会同步更新，请您耐心等待。"
            )
        return (
            f"您的订单 {order.get('order_id', '')} 当前状态为待发货，"
            "暂未产生物流轨迹。商品发货后物流信息会同步更新，请您耐心等待。"
        )
    if order and not logistics:
        status_zh = STATUS_ZH.get(order.get("status"), order.get("status"))
        return (
            f"您的订单 {order.get('order_id', '')} 当前状态为{status_zh}，"
            f"订单金额合计 {order.get('total_amount', '')} 元，"
            f"共 {len(order.get('items') or [])} 件商品。"
        )
    if order or logistics:
        # 仅有物流结果时，订单号从物流快照回填
        order_id = order.get("order_id") or logistics.get("order_id") or ""
        status_zh = (
            STATUS_ZH.get(order.get("status"), "已发货")
            if order
            else "已发货"
        )
        loc = logistics.get("current_location") or "途中"
        etd = logistics.get("estimated_delivery") or ""
        if amount and order and logistics and order.get("total_amount"):
            return (
                f"您的订单 {order_id} 当前状态为{status_zh}，"
                f"订单金额合计 {order.get('total_amount', '')} 元；"
                f"物流信息：{loc}"
                + (f"，预计 {etd} 送达。" if etd else "。")
            )
        return (
            f"您的订单 {order_id} 当前状态为{status_zh}，物流信息：{loc}"
            + (f"，预计 {etd} 送达。" if etd else "。")
        )
    if knowledge_hits:
        if doc_title:
            # v1.1 聚合前 2 条命中（【标题】正文），与阶段三模板同口径：
            # 预期关键词可能只在次序靠后的命中文档标题中（AP-POL-06 的“售后”
            # 在 policies:售后服务政策 标题、检索 top2），只渲染 top1 不可达
            parts = [
                f"【{h.get('title', '')}】{h.get('content', '')}"
                for h in knowledge_hits[:2] if h
            ]
            answer = "关于您的问题，为您查询到：" + "；".join(parts)
            if len(parts) > 1:
                answer += "。如需进一步了解，可以继续向我提问。"
            return answer
        top = knowledge_hits[0] or {}
        return f"关于您的问题，为您查询到：{top.get('content', '')}"
    return (
        "抱歉，暂时没有查询到相关信息。请确认订单号是否正确，"
        "或联系人工客服获取进一步帮助。"
    )
