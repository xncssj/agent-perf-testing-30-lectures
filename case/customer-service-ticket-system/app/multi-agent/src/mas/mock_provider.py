"""MockProvider：无密钥、确定性、可复现的模拟模型（Multi-Agent v2）。

与 v1 MockLlm 的规划/决策/校验/答复规则保持一致（意图关键词表、Agent 清单
剪枝、参数抽取、事实校验与答复文案），保证共享工单集上的业务答复可对照回归：
1. 超管规划：识别意图并分解专业 Agent 任务清单；
2. 子 Agent 任务决策：每位专业 Agent 独立产出工具调用决策；
3. 聚合校验：FactsHelper 检查事实完整性；
4. 答复生成：解析聚合事实 JSON，产出携带结构化事实的自然语言答复。

延迟按（seed + 特征键）派生，无共享随机状态，线程/协程安全且可复现。
延迟与 token 用量均为模拟数据（usage_source=estimated）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import AsyncIterator

from harness.budget import DeadlineExceeded
from harness.contracts import AnswerChunk, ModelUsage, RunContext

from .provider import (
    AgentTaskResult,
    BaseMultiAgentProvider,
    PlanResult,
    ValidationVerdict,
)

ORDER_ID_RE = re.compile(r"(?<!\d)(\d{13})(?!\d)")
TRACKING_NO_RE = re.compile(r"(?P<carrier>SF|YT|ZTO|JD|EMS)\d{6,}")

# 订单状态 -> 答复中的中文表述（与共享数据集 test-data/generate.py 的 STATUS_ZH 保持一致）
STATUS_ZH = {"pending": "待发货", "shipped": "已发货", "exception": "异常"}

# 意图 -> 专业 Agent 任务清单（Multi-Agent 规划结果，顺序即扇出轮次）
_PLAN_AGENTS: dict[str, list[str]] = {
    "logistics_query": ["order_agent", "logistics_agent"],
    "delivery_time": ["order_agent", "logistics_agent"],
    "order_info": ["order_agent"],
    "refund_status": ["order_agent"],
    "return_policy": ["knowledge_agent"],
    "exchange_policy": ["knowledge_agent"],
    "contact_info": ["knowledge_agent"],
    "complaint": [],
    "clarification": [],
}

# 专业 Agent -> 其对应工具
AGENT_TOOL: dict[str, str] = {
    "order_agent": "query_order",
    "logistics_agent": "query_logistics",
    "knowledge_agent": "search_knowledge",
    "validation_agent": "",
}


class IntentDetector:
    """基于规则的意图识别，对齐 07-testing-scenarios 的业务意图集合。"""

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
            for k in ["什么时候到", "什么时候能到", "多久到", "多久能到",
                      "预计什么时候", "何时送达", "几天能到", "什么时候送到"]
        ):
            return "delivery_time"
        if any(k in q for k in ["到哪了", "到哪", "物流", "配送", "快递", "运单", "送到了吗", "派送"]):
            return "logistics_query"
        if "订单" in q:
            return "order_info"
        return "clarification"


def fallback_plan(query: str, intent: str) -> list[str]:
    """规则回退规划：意图 -> Agent 清单（含订单号/运单号缺失剪枝）。"""
    agents = list(_PLAN_AGENTS.get(intent, []))
    has_order = bool(ORDER_ID_RE.search(query))
    has_tracking = bool(TRACKING_NO_RE.search(query))
    if intent in ("logistics_query", "delivery_time") and not (has_order or has_tracking):
        agents = [a for a in agents if a != "logistics_agent"]
    if intent in ("logistics_query", "delivery_time") and has_tracking and not has_order:
        agents = [a for a in agents if a != "order_agent"]
    return agents


def fallback_args(query: str, tool: str, v11: bool = False) -> dict:
    """按工具从工单文本中抽取参数；抽不到时返回空参数（由调用方澄清）。

    v11=True 时知识检索词走 v1.1 词表（政策类复合词优先，战役 A7）；
    缺省与 legacy 冻结口径逐字一致（openai_provider 等既有调用不受影响）。
    """
    if tool == "query_order":
        m = ORDER_ID_RE.search(query)
        return {"order_id": m.group(1)} if m else {"order_id": ""}
    if tool == "query_logistics":
        m = TRACKING_NO_RE.search(query)
        if m:
            return {"tracking_no": m.group(0)}
        m = ORDER_ID_RE.search(query)
        return {"order_id": m.group(1)} if m else {"order_id": ""}
    if tool == "search_knowledge":
        from harness.mock_provider import knowledge_query_of
        return {"query": knowledge_query_of(query, v11)}
    return {}
    return {}


def estimate_usage(prompt_text: str, completion_text: str) -> ModelUsage:
    """估算 token：中文按“字符数 / 2”近似（usage_source=estimated）。"""
    prompt = max(1, len(prompt_text) // 2)
    completion = max(1, len(completion_text) // 2)
    return ModelUsage(prompt_tokens=prompt, completion_tokens=completion,
                      total_tokens=prompt + completion, source="estimated")


class FactsHelper:
    """从多 Agent 聚合结果中抽取业务事实（供校验 Agent 与聚合复用）。"""

    @staticmethod
    def extract(facts: dict) -> dict:
        present: dict = {}
        order_w = facts.get("order") or {}
        logistics_w = facts.get("logistics") or {}
        if order_w.get("found") and order_w.get("order"):
            o = order_w["order"]
            present["order_id"] = o.get("order_id", "")
            present["status"] = o.get("status", "")
        if logistics_w.get("found") and logistics_w.get("logistics"):
            lo = logistics_w["logistics"]
            present.setdefault("order_id", lo.get("order_id", ""))
            present["current_location"] = lo.get("current_location", "")
            present["estimated_delivery"] = lo.get("estimated_delivery", "")
        return present


class MockProvider(BaseMultiAgentProvider):
    """确定性模拟模型（Multi-Agent v2）。"""

    evidence_kind = "mock"

    def __init__(self, settings) -> None:
        self.settings = settings
        self.model = settings.model_name
        # Mock 规则版本：缺省 legacy 保持冻结基线行为；stub 配置对象缺字段时同样回落
        self.mock_rule_version = getattr(settings, "mock_rule_version", "legacy") or "legacy"
        self._v11 = self.mock_rule_version == "performance-v1.1"
        self.intent = IntentDetector()

    def _latency_ms(self, key: str, base: float, jitter: float) -> float:
        rng = random.Random(self.settings.mock_seed + sum(ord(c) for c in key) * 31)
        return max(10.0, base + rng.uniform(-jitter, jitter))

    async def _sleep(self, key: str, base: float, jitter: float,
                     context: RunContext) -> float:
        """模拟延迟：整体 deadline 优先于模拟等待，返回实际等待毫秒。"""
        latency = self._latency_ms(key, base, jitter)
        remaining = context.remaining_s()
        if remaining <= 0:
            raise DeadlineExceeded()
        await asyncio.sleep(min(latency / 1000.0, remaining))
        return latency

    # ---- 超管规划 ----
    async def plan(self, query: str, context: RunContext) -> PlanResult:
        intent = self.intent.detect(query)
        agents = fallback_plan(query, intent)
        usage = estimate_usage(
            f"识别用户意图并为以下问题分配合适的专业 Agent：{query}", intent + str(agents)
        )
        usage.completion_tokens = 10
        usage.total_tokens = (usage.prompt_tokens or 0) + 10
        latency = await self._sleep(
            query, self.settings.mock_plan_latency_ms, self.settings.mock_plan_jitter_ms,
            context,
        )
        return PlanResult(intent=intent, agents=agents, model=self.model,
                          latency_ms=latency, usage=usage)

    # ---- 子 Agent 任务决策 ----
    async def agent_task(self, agent: str, query: str,
                         context: RunContext) -> AgentTaskResult:
        tool = AGENT_TOOL.get(agent, "")
        args = fallback_args(query, tool, v11=self._v11) if tool else {}
        clarify = bool(
            tool in ("query_order", "query_logistics")
            and not (args.get("order_id") or args.get("tracking_no"))
        )
        usage = estimate_usage(f"{agent}{query}", "agent_task")
        usage.completion_tokens = 8
        usage.total_tokens = (usage.prompt_tokens or 0) + 8
        latency = await self._sleep(
            agent, self.settings.mock_agent_latency_ms,
            self.settings.mock_agent_jitter_ms, context,
        )
        return AgentTaskResult(
            tool=None if (clarify or not tool) else tool,
            args=args, clarify=clarify, model=self.model,
            latency_ms=latency, usage=usage,
        )

    # ---- 聚合校验 ----
    async def validation(self, query: str, facts: dict,
                         context: RunContext) -> ValidationVerdict:
        required = ["order_id", "status", "current_location", "estimated_delivery"]
        present = FactsHelper.extract(facts)
        missing = [k for k in required if not present.get(k)]
        usage = estimate_usage(
            f"{query}{json.dumps(facts, ensure_ascii=False)}",
            "complete" if not missing else "incomplete",
        )
        usage.completion_tokens = 6
        usage.total_tokens = (usage.prompt_tokens or 0) + 6
        latency = await self._sleep(
            "validation", self.settings.mock_validation_latency_ms, 40, context
        )
        return ValidationVerdict(
            status="complete" if not missing else "incomplete",
            missing=missing, model=self.model, latency_ms=latency, usage=usage,
        )

    # ---- 答复生成 ----
    async def stream_answer(self, messages: list, context: RunContext) -> AsyncIterator[AnswerChunk]:
        if self._v11:
            answer = self._answer_v1_1(messages)
        else:
            answer = self._answer(messages)
        prompt_text = " ".join(m.content for m in messages)
        t0 = time.perf_counter()
        latency = self._latency_ms(
            "answer", self.settings.mock_plan_latency_ms, self.settings.mock_plan_jitter_ms
        )
        remaining = context.remaining_s()
        if remaining <= 0:
            raise DeadlineExceeded()
        await asyncio.sleep(min(latency / 1000.0, remaining))
        _ = t0
        token_gap = self.settings.mock_token_latency_ms / 1000.0
        for ch in answer:
            await asyncio.sleep(min(token_gap, max(0.0, context.remaining_s())))
            yield AnswerChunk(content=ch)
        yield AnswerChunk(
            content="", usage=estimate_usage(prompt_text, answer), finish=True
        )

    def _answer(self, messages: list) -> str:
        """答复生成调用的确定性输出（legacy 规则，与 v1 冻结基线一致）。

        答复必须携带结构化事实，供业务判定按 expected_facts 校验；
        降级时如实说明子服务不可用，不虚构业务事实。
        """
        order, logistics, knowledge_hits, degraded = _observations(messages, last_only=True)
        return _render_answer(order, logistics, knowledge_hits, degraded)

    def _answer_v1_1(self, messages: list) -> str:
        """performance-v1.1 答复：聚合全部子 Agent 观察，金额与物流事实同现。

        legacy 只读最后一条工具观察，复合诉求（金额 + 物流/时效）下订单金额
        会被物流观察覆盖，compound 族黄金用例的 total_amount 事实恒不可达
        （战役 A7）；v1.1 聚合订单与物流观察，在不删除既有事实断言的前提下
        使其可达。知识答复同时携带命中文档标题（部分预期关键词如 AP-POL-06
        的“售后”只在标题中）。降级答复的边界口径与 legacy 一致。
        """
        order, logistics, knowledge_hits, degraded = _observations(messages, last_only=False)
        return _render_answer(order, logistics, knowledge_hits, degraded,
                              amount=True, doc_title=True)

    async def aclose(self) -> None:
        return None


def _observations(messages: list, last_only: bool):
    """解析子 Agent 聚合观察为订单/物流/知识/降级四类数据。

    last_only=True 复刻 legacy 口径（只读最后一条工具观察）；
    last_only=False 按时间顺序聚合全部工具观察，供 v1.1 复合诉求答复使用。
    """
    tool_messages = [m for m in messages if m.role == "tool"]
    if last_only and tool_messages:
        tool_messages = tool_messages[-1:]
    order: dict = {}
    logistics: dict = {}
    knowledge_hits: list = []
    degraded: dict = {}
    for m in tool_messages:
        try:
            data = json.loads(m.content)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        order_wrapper = data.get("order") or {}
        logistics_wrapper = data.get("logistics") or {}
        knowledge_wrapper = data.get("knowledge") or {}
        degraded = data.get("degraded") or degraded
        if order_wrapper.get("found") and order_wrapper.get("order"):
            order = order_wrapper["order"]
        if logistics_wrapper.get("found") and logistics_wrapper.get("logistics"):
            logistics = logistics_wrapper["logistics"]
        if knowledge_wrapper.get("found") and knowledge_wrapper.get("hits"):
            knowledge_hits = knowledge_wrapper["hits"]
    return order, logistics, knowledge_hits, degraded


def _render_answer(
    order: dict,
    logistics: dict,
    knowledge_hits: list,
    degraded: dict | None = None,
    amount: bool = False,
    doc_title: bool = False,
) -> str:
    """按观察数据渲染确定性答复。

    amount=True 时订单与物流事实同现（v1.1）；doc_title=True 时知识答复
    携带命中文档标题（v1.1），legacy 两种口径都保持冻结基线模板。
    """
    degraded = degraded or {}
    if not (order or logistics or knowledge_hits) and degraded:
        return (
            "非常抱歉，部分子服务当前不可用，暂时无法为您核对该订单的完整信息。"
            "建议稍后重试，或联系人工客服为您处理。"
        )
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