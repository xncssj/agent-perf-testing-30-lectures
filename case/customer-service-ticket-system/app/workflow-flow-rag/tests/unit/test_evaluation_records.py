"""逐笔评测记录单元测试（006 R04 / V10—V11 的实现级口径）。

- 逐笔记录与 run_id/trace_id/dataset/规则版本相互解析；
- 缺证据字段保留空值与原因，不推断为通过；
- 汇总分母只统计适用样本，无适用样本不显示 0%/100%。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.records import (  # noqa: E402
    EVAL_RULES_VERSION,
    build_evaluation_records,
)

DATASET_SHA = "d" * 64


def _report() -> dict:
    return {
        "run_id": "benchmark-20260926",
        "fingerprint": {"slo_e2e_ms": 8000.0, "search_mode": "bm25"},
        "businesses": [
            {
                "ticket_id": "T001", "qualified": True,
                "run_id": "run-1", "batch_id": "bench_1",
                "status": "completed", "stop_reason": "answered",
                "e2e_ms": 3000.0, "tools": ["query_order"],
                "quality": {"facts_expected": 2, "facts_hit": 2,
                            "keywords_expected": 3, "keywords_missed": []},
            },
            {
                "ticket_id": "T002", "qualified": False,
                "run_id": "run-2", "batch_id": "bench_1",
                "status": "completed", "stop_reason": "answered",
                "e2e_ms": 9000.0, "tools": [],
                "quality": {"facts_expected": 1, "facts_hit": 0,
                            "keywords_expected": 2, "keywords_missed": ["x"]},
            },
            {
                "ticket_id": "T003", "qualified": True,
                "run_id": "", "batch_id": "",
                "status": "completed", "stop_reason": "clarification",
                "e2e_ms": None, "tools": [],
                "quality": {"facts_expected": 0, "facts_hit": 0,
                            "keywords_expected": 0, "keywords_missed": []},
            },
        ],
    }


class EvaluationRecordsTest(unittest.TestCase):
    def setUp(self):
        self.payload = build_evaluation_records(_report(), DATASET_SHA)

    def test_identity_linkage(self):
        r1 = self.payload["records"][0]
        self.assertEqual(r1["task_id"], "T001")
        self.assertEqual(r1["run_id"], "run-1")
        self.assertEqual(r1["trace_id"], r1["run_id"], "run_id 即 Trace ID")
        self.assertEqual(r1["dataset_sha256"], DATASET_SHA)
        self.assertEqual(r1["rule_version"], EVAL_RULES_VERSION)

    def test_missing_fields_stay_none_with_reason(self):
        # 逐笔护栏明细未持久化：安全判定保留空值与原因
        for r in self.payload["records"]:
            self.assertIsNone(r["safety"]["value"])
            self.assertIn("未逐笔持久化", r["safety"]["basis"])
        # 无事实判定依据：保留空值
        r3 = self.payload["records"][2]
        self.assertIsNone(r3["fact_correct"]["value"])
        self.assertIn("无事实判定依据", r3["fact_correct"]["basis"])
        # 缺 e2e：SLO 保留空值
        self.assertIsNone(r3["slo_met"]["value"])

    def test_rule_based_fact_judgement_labeled(self):
        r1 = self.payload["records"][0]
        self.assertTrue(r1["fact_correct"]["value"])
        self.assertIn("确定性子串判定", r1["fact_correct"]["basis"],
                      "关键词/要素命中不能冒充语义事实正确")
        r2 = self.payload["records"][1]
        self.assertFalse(r2["fact_correct"]["value"])

    def test_slo_judgement_uses_threshold(self):
        self.assertTrue(self.payload["records"][0]["slo_met"]["value"])
        self.assertFalse(self.payload["records"][1]["slo_met"]["value"])

    def test_summary_denominators(self):
        summary = self.payload["summary"]
        # fact_correct：T001/T002 适用 → 1/2；T003 不适用不计分母
        self.assertEqual(summary["fact_correct"]["numerator"], 1)
        self.assertEqual(summary["fact_correct"]["denominator"], 2)
        # task_completion：三者都适用 → 2/3
        self.assertEqual(summary["task_completion"]["denominator"], 3)
        self.assertEqual(summary["task_completion"]["numerator"], 2)
        # slo：T003 缺 e2e 不适用 → 1/2
        self.assertEqual(summary["slo_met"]["denominator"], 2)

    def test_empty_applicable_not_zero_percent(self):
        payload = build_evaluation_records(
            {"run_id": "r", "fingerprint": {}, "businesses": []}, DATASET_SHA)
        summary = payload["summary"]
        self.assertEqual(summary["task_completion"]["denominator"], 0)
        self.assertIsNone(summary["task_completion"]["rate"])
        self.assertIn("无适用样本", summary["task_completion"]["note"])


if __name__ == "__main__":
    unittest.main()
