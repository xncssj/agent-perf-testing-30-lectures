"""Workflow+RAG Mock 规则版本（MOCK_RULE_VERSION）单元测试。

007 数据档战役整改 R02：legacy 缺省行为不变；performance-v1.1 在订单与物流
事实同现时输出金额，使 compound 族黄金用例的 total_amount 事实在答复中可达。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_APP_ROOT = PROJECT_ROOT.parent  # app 目录：共享 harness 包所在目录
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from src.llm.mock_llm import MockLlm  # noqa: E402

ORDER_ID = "2024010100031"


def make_settings(**kw):
    base = dict(
        model_name="mock", mock_seed=42,
        intent_latency_ms=1, intent_jitter_ms=1,
        generation_latency_ms=1, generation_jitter_ms=1,
        clarification_latency_ms=1, clarification_jitter_ms=1,
        token_latency_ms=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def facts_with(amount=2999):
    return {
        "order": {"found": True, "order": {
            "order_id": ORDER_ID, "status": "shipped", "total_amount": amount,
            "items": [],
        }},
        "logistics": {"found": True, "logistics": {
            "order_id": ORDER_ID, "current_location": "上海分拨中心",
            "estimated_delivery": "明天 20:00 前",
        }},
    }


# 知识命中的正文不含预期关键词（“售后”只在文档标题中，战役 A7 AP-POL-06 形态）
KNOWLEDGE_HITS = [{
    "doc_id": "policies:售后服务政策", "title": "售后服务政策",
    "content": "自签收之日起 7 天内享受无理由退换货。",
}]


class TestLegacyAnswerUnchanged(unittest.TestCase):
    def setUp(self):
        self.llm = MockLlm(make_settings())

    def test_default_rule_is_legacy(self):
        self.assertEqual(self.llm.mock_rule_version, "legacy")

    def test_compound_answer_omits_amount(self):
        # legacy 模板不输出金额（战役 A7 现象保留）
        answer = self.llm.generate_answer(
            f"订单 {ORDER_ID} 金额对吗，物流现在到哪？", facts_with(), []).content
        self.assertIn("上海分拨中心", answer)
        self.assertNotIn("2999", answer)

    def test_knowledge_answer_omits_doc_title(self):
        # legacy 知识答复只输出正文，不携带文档标题（冻结基线口径）
        answer = self.llm.generate_answer(
            "请介绍售后退货政策。", {}, KNOWLEDGE_HITS).content
        self.assertIn("自签收之日起 7 天内享受无理由退换货", answer)
        self.assertNotIn("售后服务政策", answer)


class TestV11Answer(unittest.TestCase):
    def setUp(self):
        self.llm = MockLlm(make_settings(mock_rule_version="performance-v1.1"))

    def test_compound_answer_carries_amount_and_logistics(self):
        answer = self.llm.generate_answer(
            f"订单 {ORDER_ID} 金额对吗，物流现在到哪？", facts_with(), []).content
        self.assertIn("已发货", answer)
        self.assertIn("2999", answer)
        self.assertIn("上海分拨中心", answer)
        self.assertIn("预计", answer)

    def test_order_only_answer_keeps_amount_template(self):
        facts = facts_with()
        facts["logistics"] = {"found": False}
        answer = self.llm.generate_answer(
            f"请核对订单 {ORDER_ID} 的实际金额。", facts, []).content
        self.assertIn("2999", answer)

    def test_knowledge_answer_carries_doc_title(self):
        # v1.1 知识答复携带命中文档标题：预期关键词“售后”只在标题中（战役 A7）
        answer = self.llm.generate_answer(
            "请介绍售后退货政策。", {}, KNOWLEDGE_HITS).content
        self.assertIn("【售后服务政策】", answer)
        self.assertIn("自签收之日起 7 天内享受无理由退换货", answer)


if __name__ == "__main__":
    unittest.main()
