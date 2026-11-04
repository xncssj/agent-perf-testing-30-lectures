"""MockLlm：Workflow+RAG 阶段的确定性、无密钥模拟 LLM。

相比案例二新增/收敛的角色只有三个：意图识别节点、答复生成节点、澄清节点。
LLM 调用次数从 Multi-Agent 的 5 类收敛为 2~3 次（意图 + 生成 / 澄清），
是"节点化后调用瘦身"的量化依据。

延迟由 workflow_config.yaml 的 llm.mock 段控制，按（seed+特征键）派生，
无共享随机状态，线程安全。接入真实模型见 openai_llm.py（LLM_PROVIDER=openai）。
"""
from __future__ import annotations

import random
import re
import time
import sys
from pathlib import Path

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.tokenization import token_count, tokenize  # noqa: E402

from ..llm.base import BaseLLMClient, LLMResult, Message, ms

ORDER_ID_RE = re.compile(r"(?<!\d)(\d{13})(?!\d)")
TRACKING_NO_RE = re.compile(r"(?P<carrier>SF|YT|ZTO|JD|EMS)\d{6,}")

STATUS_ZH = {"pending": "待发货", "shipped": "已发货", "exception": "异常"}

# 澄清意图关键词：命中但缺少订单号等要素时回退澄清
_CLARIFY_HINTS = ["推荐", "打折", "优惠", "活动", "怎么", "如何", "吗", "吗？", "吗?"]


class IntentDetector:
    """基于规则的意图识别，与案例一/二同一业务意图集合。

    补充 Workflow 时代的口径：物流/订单/时效类意图若缺少订单号与运单号，
    一律回退为 clarification（命中数据契约里 M 系列澄清工单的分支）。
    """

    # 依赖单号要素方能落地的意图
    ID_NEEDED = {"logistics_query", "delivery_time", "order_info", "refund_status"}

    def detect(self, query: str) -> str:
        q = query.lower()
        if re.search(r"退款.*(到哪|进度|状态|到账)", q):
            intent = "refund_status"
        elif any(k in q for k in ["投诉", "投诉问题"]):
            intent = "complaint"
        elif "换货" in q:
            intent = "exchange_policy"
        elif "退货" in q or "退款" in q:
            intent = "return_policy"
        elif any(k in q for k in ["电话", "400", "联系方式"]):
            intent = "contact_info"
        elif any(k in q for k in ["什么时候到", "什么时候能到", "多久到", "多久能到",
                                  "预计什么时候", "何时送达", "几天能到", "什么时候送到"]):
            intent = "delivery_time"
        elif any(k in q for k in ["到哪了", "到哪", "物流", "配送", "快递", "运单", "送到了吗", "派送"]):
            intent = "logistics_query"
        elif "订单" in q:
            intent = "order_info"
        else:
            intent = "clarification"
        if intent in self.ID_NEEDED and not (ORDER_ID_RE.search(query) or TRACKING_NO_RE.search(query)):
            return "clarification"
        return intent


class MockLlm(BaseLLMClient):
    """确定性模拟模型（Workflow 节点消费）。"""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        seed = self.settings.mock_seed
        self._rng = random.Random(seed)
        # Mock 规则版本：缺省 legacy 保持冻结基线行为；stub 配置对象缺字段时同样回落
        self.mock_rule_version = getattr(settings, "mock_rule_version", "legacy") or "legacy"
        self._v11 = self.mock_rule_version == "performance-v1.1"
        self.intent = IntentDetector()

    ###########################################################################
    # 可复现延迟
    ###########################################################################
    def _latency_ms(self, key: str, base: float, jitter: float) -> float:
        rng = random.Random(self.settings.mock_seed + sum(ord(c) for c in key) * 31)
        return max(10.0, base + rng.uniform(-jitter, jitter))

    def _intent_latency(self, query: str) -> float:
        return self._latency_ms(query, self.settings.intent_latency_ms, self.settings.intent_jitter_ms)

    def _generation_latency(self, key: str) -> float:
        return self._latency_ms(key, self.settings.generation_latency_ms, self.settings.generation_jitter_ms)

    def _clarification_latency(self, key: str) -> float:
        return self._latency_ms(key, self.settings.clarification_latency_ms, self.settings.clarification_jitter_ms)

    ###########################################################################
    # 意图识别节点
    ###########################################################################
    def recognize_intent(self, query: str) -> LLMResult:
        """意图识别：输出 intent + 置信度 + 抽出的订单/运单要素。"""
        t0 = time.perf_counter()
        intent = self.intent.detect(query)
        has_order = bool(ORDER_ID_RE.search(query))
        has_tracking = bool(TRACKING_NO_RE.search(query))
        # 免单号白名单与 KNOWLEDGE_INTENTS 对齐：知识类咨询（退货/换货/联系渠道）
        # 无需订单号即可答复，缺单号不应压低置信度触发人工转接
        resolve_ok = has_order or has_tracking or intent in ("return_policy", "exchange_policy", "contact_info", "clarification")
        if intent == "complaint":
            confidence = 0.5
        else:
            confidence = 0.98 if resolve_ok else 0.6
        result = LLMResult(model=self.model, streamed=False)
        result.content = intent
        result.raw = {
            "intent": intent,
            "confidence": confidence,
            "has_order": has_order,
            "has_tracking": has_tracking,
        }
        result.prompt_tokens = max(1, token_count(query))
        result.completion_tokens = 10
        result.total_tokens = result.prompt_tokens + result.completion_tokens
        time.sleep(self._intent_latency(query) / 1000.0)
        result.latency_ms = ms(t0)
        result.ttft_ms = result.latency_ms
        return result

    ###########################################################################
    # 答复生成节点
    ###########################################################################
    def generate_answer(self, query: str, facts: dict, retrieval_hits: list) -> LLMResult:
        """答复生成：按可用的结构化事实或 RAG 命中文档渲染答复。

        事实优先于知识库：有订单/物流数据时输出业务事实答复，
        否则用知识库命中输出知识答复；两者皆无时给出澄清/致歉。
        """
        t0 = time.perf_counter()
        order = (facts.get("order") or {}).get("order") or {}
        logistics = (facts.get("logistics") or {}).get("logistics") or {}
        degraded = facts.get("degraded") or {}
        # v1.1 规则在订单与物流事实同现时输出金额（legacy 模板保持冻结基线口径）
        with_amount = self._v11 and bool(order) and bool(order.get("total_amount"))

        if not (order or logistics) and degraded:
            answer = (
                "非常抱歉，部分数据服务当前不可用，暂时无法为您核对该订单的完整信息。"
                "建议稍后重试，或联系人工客服为您处理。"
            )
        elif order.get("status") == "pending" and not logistics:
            if with_amount:
                answer = (
                    f"您的订单 {order.get('order_id', '')} 当前状态为待发货，"
                    f"订单金额合计 {order.get('total_amount', '')} 元。"
                    "商品发货后物流信息会同步更新，请您耐心等待。"
                )
            else:
                answer = (
                    f"您的订单 {order.get('order_id', '')} 当前状态为待发货，"
                    "暂未产生物流轨迹。商品发货后物流信息会同步更新，请您耐心等待。"
                )
        elif order and not logistics:
            status_zh = STATUS_ZH.get(order.get("status"), order.get("status"))
            answer = (
                f"您的订单 {order.get('order_id', '')} 当前状态为{status_zh}，"
                f"订单金额合计 {order.get('total_amount', '')} 元，"
                f"共 {len(order.get('items') or [])} 件商品。"
            )
        elif order or logistics:
            order_id = order.get("order_id") or logistics.get("order_id") or ""
            status_zh = STATUS_ZH.get(order.get("status"), "已发货") if order else "已发货"
            loc = logistics.get("current_location") or "途中"
            etd = logistics.get("estimated_delivery") or ""
            if with_amount and logistics:
                answer = (
                    f"您的订单 {order_id} 当前状态为{status_zh}，"
                    f"订单金额合计 {order.get('total_amount', '')} 元；"
                    f"物流信息：{loc}"
                    + (f"，预计 {etd} 送达。" if etd else "。")
                )
            else:
                answer = (
                    f"您的订单 {order_id} 当前状态为{status_zh}，物流信息：{loc}"
                    + (f"，预计 {etd} 送达。" if etd else "。")
                )
        elif retrieval_hits:
            # v1.1 规则同时输出命中文档标题：部分预期关键词（如 AP-POL-06 的
            # “售后”）只出现在文档标题中，仅输出正文会使其确定不可达（战役 A7）；
            # legacy 模板保持冻结基线口径（仅正文）。
            if self._v11:
                top_texts = [
                    f"【{h.get('title', '')}】{h.get('content', '')}"
                    for h in retrieval_hits[:2]
                ]
            else:
                top_texts = [h.get("content", "") for h in retrieval_hits[:2]]
            answer = "关于您的问题，为您查询到：" + "；".join(top_texts)
            if len(top_texts) > 1:
                answer += "。如需进一步了解，可以继续向我提问。"
        else:
            answer = (
                "抱歉，暂时没有查询到相关信息。请确认订单号是否正确，"
                "或联系人工客服获取进一步帮助。"
            )

        result = LLMResult(model=self.model, streamed=False)
        result.content = answer
        result.prompt_tokens = max(1, token_count(query) + len(retrieval_hits) * 30)
        result.completion_tokens = max(1, token_count(answer))
        result.total_tokens = result.prompt_tokens + result.completion_tokens
        time.sleep(self._generation_latency(query) / 1000.0)
        result.latency_ms = ms(t0)
        result.ttft_ms = result.latency_ms
        return result

    ###########################################################################
    # 澄清节点
    ###########################################################################
    def clarify(self, query: str) -> LLMResult:
        """澄清答复：要素不足时要求补充订单号/运单号。"""
        t0 = time.perf_counter()
        answer = (
            "为了帮您准确查询，请提供订单号或物流单号。"
            "您可以直接回复 13 位订单号，我们将继续为您处理。"
        )
        result = LLMResult(model=self.model, streamed=False)
        result.content = answer
        result.raw = {"intent": "clarification"}
        result.prompt_tokens = max(1, token_count(query))
        result.completion_tokens = max(1, token_count(answer))
        result.total_tokens = result.prompt_tokens + result.completion_tokens
        time.sleep(self._clarification_latency(query) / 1000.0)
        result.latency_ms = ms(t0)
        result.ttft_ms = result.latency_ms
        return result

    ###########################################################################
    # 统一 complete 接口（分流到节点语义；供 OpenAILlm 之外的重放使用）
    ###########################################################################
    def complete(self, messages, temperature=None, max_tokens=None) -> LLMResult:
        user_msg = next((m for m in messages if m.role == "user"), None)
        return LLMResult(model=self.model, content=(user_msg.content if user_msg else ""),
                         total_tokens=0, latency_ms=0.0)

    async def stream(self, messages, temperature=None, max_tokens=None):
        result = self.complete(messages)
        # 按确定性 token 边界逐个产出（与阶段一/二同一实现：
        # harness/tokenization.py），使"一个流式片段 = 一个 token"在 Mock
        # 口径下成立，采集到的间隔才是 token 间隔而非字符间隔（待办 2a）
        for token in tokenize(result.content or ""):
            yield token
            time.sleep(self.settings.token_latency_ms / 1000.0)