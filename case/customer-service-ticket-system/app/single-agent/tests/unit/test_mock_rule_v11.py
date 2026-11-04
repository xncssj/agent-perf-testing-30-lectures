"""Mock 规则版本（MOCK_RULE_VERSION）单元测试：legacy 冻结基线与 v1.1 金额/复合路径。

007 数据档战役整改 R02：legacy 缺省行为必须与冻结基线逐字一致；
performance-v1.1 恢复金额（total_amount）与复合诉求（compound 族）的
确定性工具路径与答复事实，不删除既有断言。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.harness.contracts import Message, RunContext  # noqa: E402
from src.llm.mock_provider import IntentDetector, MockProvider  # noqa: E402

ORDER_ID = "2024010100002"


def make_ctx(query: str = "") -> RunContext:
    import time

    return RunContext(task_id="T", run_id="R", query=query,
                      deadline=time.monotonic() + 30)


def make_settings(**kw):
    base = dict(
        model_name="mock", mock_seed=42, mock_plan_latency_ms=1,
        mock_plan_jitter_ms=1, mock_token_latency_ms=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


ORDER_OBS = json.dumps({"order": {"found": True, "order": {
    "order_id": ORDER_ID, "status": "shipped", "total_amount": 29998, "items": [],
}}, "logistics": {"found": False}, "knowledge": {"found": False}}, ensure_ascii=False)
LOGISTICS_OBS = json.dumps({"order": {"found": False}, "logistics": {
    "found": True, "logistics": {
        "order_id": ORDER_ID, "current_location": "上海分拨中心",
        "estimated_delivery": "明天 20:00 前",
    },
}, "knowledge": {"found": False}}, ensure_ascii=False)
# 知识命中的正文不含预期关键词（“售后”只在文档标题中，战役 A7 AP-POL-06 形态）
KNOWLEDGE_OBS = json.dumps({"order": {"found": False}, "logistics": {"found": False},
    "knowledge": {"found": True, "hits": [{
        "doc_id": "policies:售后服务政策", "title": "售后服务政策",
        "content": "自签收之日起 7 天内享受无理由退换货。",
    }]}}, ensure_ascii=False)


def history_with(query: str, observations: list[str]) -> list[Message]:
    messages = [Message(role="system", content="sys"),
                Message(role="user", content=query)]
    for content in observations:
        messages.append(Message(role="tool", content=content))
    return messages


class TestLegacyRuleUnchanged(unittest.TestCase):
    """缺省（未设置 MOCK_RULE_VERSION）时保持 v1 冻结基线行为。"""

    def setUp(self):
        self.provider = MockProvider(make_settings())

    def test_default_rule_is_legacy(self):
        self.assertEqual(self.provider.mock_rule_version, "legacy")

    def test_amount_query_still_falls_to_logistics_default(self):
        # v1 等价缺省分支：金额问题仍归 logistics_query（工具序列含两查询）
        detector = IntentDetector()
        self.assertEqual(detector.detect("请核对订单 2024010100002 的实际金额。"),
                         "logistics_query")

    def test_compound_answer_omits_amount(self):
        # legacy 只读最后一条工具观察：物流观察覆盖订单金额（战役 A7 现象保留）
        answer = self.provider._answer(history_with(
            f"订单 {ORDER_ID} 金额对吗，物流现在到哪？", [ORDER_OBS, LOGISTICS_OBS]))
        self.assertIn("上海分拨中心", answer)
        self.assertNotIn("29998", answer)

    def test_knowledge_answer_omits_doc_title(self):
        # legacy 知识答复只输出正文，不携带文档标题（冻结基线口径）
        answer = self.provider._answer(history_with(
            "请介绍售后退货政策。", [KNOWLEDGE_OBS]))
        self.assertIn("自签收之日起 7 天内享受无理由退换货", answer)
        self.assertNotIn("售后服务政策", answer)


class TestV11Rule(unittest.TestCase):
    """performance-v1.1：金额与复合诉求的确定性路径。"""

    def setUp(self):
        self.provider = MockProvider(make_settings(mock_rule_version="performance-v1.1"))

    def _decide(self, query: str, history=None):
        messages = history or [Message(role="system", content="sys"),
                               Message(role="user", content=query)]
        return asyncio.run(self.provider.decide(messages, {}, make_ctx(query)))

    def test_amount_only_query_plans_order_tool(self):
        q = f"请核对订单 {ORDER_ID} 的实际金额。"
        self.assertEqual(self.provider._detect(q), "order_info")
        decision = self._decide(q)
        self.assertEqual(decision.action, "call_tools")
        self.assertEqual(decision.tool_calls[0].name, "query_order")
        self.assertEqual(decision.tool_calls[0].arguments.get("order_id"), ORDER_ID)

    def test_compound_query_keeps_logistics_plan(self):
        q = f"订单 {ORDER_ID} 金额对吗，物流现在到哪？"
        self.assertEqual(self.provider._detect(q), "logistics_query")
        d1 = self._decide(q)
        self.assertEqual(d1.tool_calls[0].name, "query_order")
        history = [Message(role="system", content="sys"),
                   Message(role="user", content=q),
                   Message(role="assistant", content="", tool_calls=d1.tool_calls),
                   Message(role="tool", content=ORDER_OBS)]
        d2 = self._decide(q, history)
        self.assertEqual(d2.tool_calls[0].name, "query_logistics")

    def test_compound_answer_carries_amount_and_logistics(self):
        answer = self.provider._answer_v1_1(history_with(
            f"订单 {ORDER_ID} 金额对吗，物流现在到哪？", [ORDER_OBS, LOGISTICS_OBS]))
        self.assertIn("已发货", answer)
        self.assertIn("29998", answer)
        self.assertIn("上海分拨中心", answer)
        self.assertIn("明天 20:00 前", answer)

    def test_amount_only_answer_carries_amount(self):
        answer = self.provider._answer_v1_1(history_with(
            f"请核对订单 {ORDER_ID} 的实际金额。", [ORDER_OBS]))
        self.assertIn("29998", answer)
        self.assertIn("已发货", answer)

    def test_policy_intent_ignores_amount_word(self):
        self.assertEqual(self.provider._detect("退货政策里金额部分怎么算"),
                         "return_policy")

    def test_v11_knowledge_query_prefers_policy_phrase(self):
        # v1.1 政策类复合词优先：让“售后”预期词所在政策文档可达（战役 A7）
        self.assertEqual(
            self.provider._take_tool_args("请介绍售后退货政策。", "search_knowledge"),
            {"query": "退货政策"})
        # legacy 抽取与冻结基线逐字一致：同一工单仍抽取单词“退货”
        legacy = MockProvider(make_settings())
        self.assertEqual(
            legacy._take_tool_args("请介绍售后退货政策。", "search_knowledge"),
            {"query": "退货"})

    def test_pending_order_answer_carries_amount(self):
        pending_obs = json.dumps({"order": {"found": True, "order": {
            "order_id": ORDER_ID, "status": "pending", "total_amount": 749,
        }}, "logistics": {"found": False}, "knowledge": {"found": False}},
            ensure_ascii=False)
        answer = self.provider._answer_v1_1(history_with(
            f"订单 {ORDER_ID} 花了多少钱？", [pending_obs]))
        self.assertIn("待发货", answer)
        self.assertIn("749", answer)

    def test_knowledge_answer_carries_doc_title(self):
        # v1.1 知识答复携带命中文档标题：预期关键词“售后”只在标题中（战役 A7）
        answer = self.provider._answer_v1_1(history_with(
            "请介绍售后退货政策。", [KNOWLEDGE_OBS]))
        self.assertIn("【售后服务政策】", answer)
        self.assertIn("自签收之日起 7 天内享受无理由退换货", answer)


if __name__ == "__main__":
    unittest.main()
