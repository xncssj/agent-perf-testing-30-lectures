"""预算控制单元测试：轮次、工具尝试与单调时钟期限（H06 部分）。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.harness.budget import Budget, BudgetExceeded, DeadlineExceeded


class TestBudget(unittest.TestCase):
    def test_iterations_consumed(self):
        b = Budget(max_iterations=3, max_tool_calls=5, timeout_s=10)
        for _ in range(3):
            b.begin_iteration()
        with self.assertRaises(BudgetExceeded) as ctx:
            b.begin_iteration()
        self.assertEqual(ctx.exception.reason, "max_iterations")

    def test_tool_attempts_consumed(self):
        b = Budget(max_iterations=10, max_tool_calls=2, timeout_s=10)
        self.assertEqual(b.consume_tool_attempt(), 1)
        self.assertEqual(b.consume_tool_attempt(), 2)
        with self.assertRaises(BudgetExceeded) as ctx:
            b.consume_tool_attempt()
        self.assertEqual(ctx.exception.reason, "max_tool_calls")

    def test_deadline_enforced(self):
        b = Budget(max_iterations=10, max_tool_calls=10, timeout_s=0.05)
        time.sleep(0.08)
        with self.assertRaises(DeadlineExceeded):
            b.begin_iteration()
        with self.assertRaises(DeadlineExceeded):
            b.consume_tool_attempt()

    def test_remaining_and_counters(self):
        b = Budget(max_iterations=5, max_tool_calls=5, timeout_s=1.0)
        b.begin_iteration()
        b.consume_tool_attempt()
        counters = b.counters()
        self.assertEqual(counters["iterations_used"], 1)
        self.assertEqual(counters["tool_attempts_used"], 1)
        self.assertEqual(counters["max_iterations"], 5)
        self.assertGreater(b.remaining_ms(), 0)
        self.assertLessEqual(b.remaining_s(), 1.0)

    def test_tool_attempt_checks_deadline_first(self):
        """deadline 优先于再次尝试：期限已过时不再消耗尝试次数。"""
        b = Budget(max_iterations=10, max_tool_calls=5, timeout_s=0.05)
        time.sleep(0.08)
        with self.assertRaises(DeadlineExceeded):
            b.consume_tool_attempt()
        self.assertEqual(b.tool_attempts_used, 0)


if __name__ == "__main__":
    unittest.main()
