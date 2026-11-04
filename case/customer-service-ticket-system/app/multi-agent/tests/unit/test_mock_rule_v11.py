"""Multi-Agent Mock 规则版本（MOCK_RULE_VERSION）单元测试。

007 数据档战役整改 R02：legacy 缺省行为不变；performance-v1.1 聚合子 Agent
观察，使 compound 族黄金用例的 total_amount 事实在答复中可达。
"""
from __future__ import annotations

import json
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

from harness.contracts import Message  # noqa: E402
from src.mas.mock_provider import MockProvider, fallback_args  # noqa: E402

ORDER_ID = "2024010100031"


def make_settings(**kw):
    base = dict(
        model_name="mock", mock_seed=42,
        mock_plan_latency_ms=1, mock_plan_jitter_ms=1,
        mock_agent_latency_ms=1, mock_agent_jitter_ms=1,
        mock_validation_latency_ms=1, mock_token_latency_ms=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


ORDER_OBS = json.dumps({"order": {"found": True, "order": {
    "order_id": ORDER_ID, "status": "shipped", "total_amount": 2999,
}}, "logistics": {"found": False}}, ensure_ascii=False)
LOGISTICS_OBS = json.dumps({"order": {"found": False}, "logistics": {
    "found": True, "logistics": {
        "order_id": ORDER_ID, "current_location": "上海分拨中心",
        "estimated_delivery": "明天 20:00 前",
    },
}}, ensure_ascii=False)
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


class TestLegacyAnswerUnchanged(unittest.TestCase):
    def setUp(self):
        self.provider = MockProvider(make_settings())

    def test_default_rule_is_legacy(self):
        self.assertEqual(self.provider.mock_rule_version, "legacy")

    def test_compound_answer_omits_amount(self):
        # legacy 只读最后一条工具观察：物流观察覆盖订单金额（战役 A7 现象保留）
        answer = self.provider._answer(history_with(
            f"订单 {ORDER_ID} 金额对吗，物流现在到哪？", [ORDER_OBS, LOGISTICS_OBS]))
        self.assertIn("上海分拨中心", answer)
        self.assertNotIn("2999", answer)

    def test_knowledge_answer_omits_doc_title(self):
        # legacy 知识答复只输出正文，不携带文档标题（冻结基线口径）
        answer = self.provider._answer(history_with(
            "请介绍售后退货政策。", [KNOWLEDGE_OBS]))
        self.assertIn("自签收之日起 7 天内享受无理由退换货", answer)
        self.assertNotIn("售后服务政策", answer)


class TestV11Answer(unittest.TestCase):
    def setUp(self):
        self.provider = MockProvider(make_settings(mock_rule_version="performance-v1.1"))

    def test_compound_answer_carries_amount_and_logistics(self):
        answer = self.provider._answer_v1_1(history_with(
            f"订单 {ORDER_ID} 金额对吗，物流现在到哪？", [ORDER_OBS, LOGISTICS_OBS]))
        self.assertIn("已发货", answer)
        self.assertIn("2999", answer)
        self.assertIn("上海分拨中心", answer)
        self.assertIn("明天 20:00 前", answer)

    def test_degraded_boundary_unchanged(self):
        degraded_obs = json.dumps({
            "order": {"found": False}, "logistics": {"found": False},
            "degraded": {"agents": {"logistics_agent": "timeout"}},
        }, ensure_ascii=False)
        answer = self.provider._answer_v1_1(history_with(
            f"订单 {ORDER_ID} 金额和物流都查一下", [degraded_obs]))
        self.assertIn("部分子服务当前不可用", answer)

    def test_knowledge_answer_carries_doc_title(self):
        # v1.1 知识答复携带命中文档标题：预期关键词“售后”只在标题中（战役 A7）
        answer = self.provider._answer_v1_1(history_with(
            "请介绍售后退货政策。", [KNOWLEDGE_OBS]))
        self.assertIn("【售后服务政策】", answer)
        self.assertIn("自签收之日起 7 天内享受无理由退换货", answer)

    def test_v11_knowledge_query_prefers_policy_phrase(self):
        # v1.1 政策类复合词优先：让“售后”预期词所在政策文档可达（战役 A7）
        self.assertEqual(fallback_args("请介绍售后退货政策。", "search_knowledge", v11=True),
                         {"query": "退货政策"})
        # legacy 抽取与冻结基线逐字一致；缺省参数同样走 legacy
        self.assertEqual(fallback_args("请介绍售后退货政策。", "search_knowledge"),
                         {"query": "退货"})


if __name__ == "__main__":
    unittest.main()
