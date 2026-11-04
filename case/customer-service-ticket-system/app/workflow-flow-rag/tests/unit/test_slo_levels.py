"""双指标时延 SLO 单元测试：business_levels 与 classify 的首字判定口径。

口径依据课程正文对应段落：v2 流式口径下完整答复 E2E 与首字（TTFT）双门槛；
首字实测缺失（v1 入口形态）时首字项不参与判定。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.metrics import business_levels  # noqa: E402
from tests.performance.common import Ticket, classify  # noqa: E402


def make_ticket() -> Ticket:
    return Ticket(
        ticket_id="T-SLO-1", user_query="我的订单 2024010100001 到哪了？",
        expected_intent="logistics_query",
        expected_tools=["query_order", "query_logistics"],
        expected_branch="auto_reply",
        expected_facts={"状态": "已发货"},
    )


class TestBusinessLevels(unittest.TestCase):
    def test_dual_metric_both_pass(self):
        levels = business_levels(
            request_ok=True, flow_done=True, business_done=True, result_ok=True,
            latency_ms=7000.0, slo_ms=8000.0,
            first_answer_ms=1500.0, first_answer_slo_ms=2000.0,
        )
        self.assertTrue(levels["slo_ok"])
        self.assertTrue(levels["first_answer_ok"])
        self.assertTrue(levels["qualified"])

    def test_dual_metric_first_answer_fail(self):
        levels = business_levels(
            request_ok=True, flow_done=True, business_done=True, result_ok=True,
            latency_ms=7000.0, slo_ms=8000.0,
            first_answer_ms=2500.0, first_answer_slo_ms=2000.0,
        )
        self.assertFalse(levels["slo_ok"])
        self.assertFalse(levels["first_answer_ok"])
        self.assertFalse(levels["qualified"])

    def test_first_answer_missing_is_not_applicable(self):
        """首字缺失（v1 形态）：首字项不参与判定，仅 E2E 生效。"""
        levels = business_levels(
            request_ok=True, flow_done=True, business_done=True, result_ok=True,
            latency_ms=7000.0, slo_ms=8000.0, first_answer_slo_ms=2000.0,
        )
        self.assertIsNone(levels["first_answer_ok"])
        self.assertTrue(levels["slo_ok"])
        self.assertTrue(levels["qualified"])


class TestClassifyFirstAnswer(unittest.TestCase):
    def test_v2_result_first_answer_enforced(self):
        ticket = make_ticket()
        result = {
            "status": "completed",
            "response": "您的订单 2024010100001 当前状态为已发货。",
            "intent": "logistics_query",
            "path": ["intent_recognition", "data_retrieval",
                     "response_generation", "confidence_check"],
            "branch": "auto_reply",
            "tool_calls": [{"tool": "query_order"}, {"tool": "query_logistics"}],
            "e2e_ms": 6500.0,
            "metrics": {"first_answer_ms": 1200.0, "total_tokens": 60},
        }
        biz = classify(ticket, result, slo_ms=8000.0, first_answer_slo_ms=2000.0)
        self.assertTrue(biz["levels"]["qualified"])
        self.assertEqual(biz["metrics"]["first_answer_ms"], 1200.0)

    def test_v2_result_first_answer_over_slo_fails(self):
        ticket = make_ticket()
        result = {
            "status": "completed",
            "response": "您的订单 2024010100001 当前状态为已发货。",
            "intent": "logistics_query",
            "path": ["intent_recognition", "data_retrieval",
                     "response_generation", "confidence_check"],
            "branch": "auto_reply",
            "tool_calls": [{"tool": "query_order"}, {"tool": "query_logistics"}],
            "e2e_ms": 6500.0,
            "metrics": {"first_answer_ms": 2600.0, "total_tokens": 60},
        }
        biz = classify(ticket, result, slo_ms=8000.0, first_answer_slo_ms=2000.0)
        self.assertFalse(biz["levels"]["slo_ok"])
        self.assertFalse(biz["levels"]["first_answer_ok"])
        self.assertFalse(biz["levels"]["qualified"])

    def test_v1_result_without_first_answer_skips(self):
        """v1 形态（无 metrics.first_answer_ms）：首字项自动跳过。"""
        ticket = make_ticket()
        result = {
            "response": "您的订单 2024010100001 当前状态为已发货。",
            "intent": "logistics_query",
            "path": ["intent_recognition", "data_retrieval",
                     "response_generation", "confidence_check"],
            "branch": "auto_reply",
            "trace": {"tool_calls": [{"tool": "query_order"},
                                     {"tool": "query_logistics"}]},
            "e2e_ms": 4900.0,
            "total_tokens": 60,
        }
        biz = classify(ticket, result, slo_ms=5000.0, first_answer_slo_ms=2000.0)
        self.assertIsNone(biz["levels"]["first_answer_ok"])
        self.assertTrue(biz["levels"]["qualified"])


if __name__ == "__main__":
    unittest.main()
