"""重试计数入轨迹的单元测试（2026-09-12 待办 1）。

口径（第 06、07 讲）：工具调用记录需单列"本次调用尝试了几次"，供
"重试增加"信号与调用放大系数回填：
- attempts = 1 表示首次即成功；> 1 表示发生了自动重试；
- 跨调用的全局尝试计数（含重试放大）由预算的 tool_attempts 承担，
  两者不可混用。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.harness.contracts import RunResult  # noqa: E402
from src.agent.core import _to_agent_result  # noqa: E402


def make_run_result(tool_calls: list[dict]) -> RunResult:
    return RunResult(
        task_id="T1",
        run_id="R1",
        status="completed",
        stop_reason="answered",
        response="您的订单已发货。",
        intent="logistics_query",
        metrics={"total_tokens": 10},
        trace={"tool_calls": tool_calls},
    )


class TestToolCallAttempts(unittest.TestCase):
    def test_first_try_records_one_attempt(self):
        result = make_run_result([
            {"tool": "query_order", "attempt": 1, "duration_ms": 12.0,
             "data": {"found": True}},
        ])
        info = _to_agent_result(result, "q").tool_calls[0]
        self.assertEqual(1, info.attempts)

    def test_retried_call_records_total_attempts(self):
        result = make_run_result([
            {"tool": "query_order", "attempt": 3, "duration_ms": 88.0,
             "data": {"found": True}, "error": None},
        ])
        info = _to_agent_result(result, "q").tool_calls[0]
        self.assertEqual(3, info.attempts)

    def test_missing_attempt_defaults_to_one(self):
        """旧报告/未回填场景按 1 次计，不冒充重试。"""
        result = make_run_result([
            {"tool": "query_order", "duration_ms": 10.0, "data": {}},
        ])
        info = _to_agent_result(result, "q").tool_calls[0]
        self.assertEqual(1, info.attempts)

    def test_amplification_factor_derivable(self):
        """调用放大系数 = 总尝试次数 ÷ 逻辑调用数。"""
        result = make_run_result([
            {"tool": "query_order", "attempt": 2, "data": {}},
            {"tool": "query_logistics", "attempt": 1, "data": {}},
        ])
        calls = _to_agent_result(result, "q").tool_calls
        total = sum(c.attempts for c in calls)
        self.assertEqual(3, total)
        self.assertAlmostEqual(1.5, total / len(calls))

    def test_tool_result_to_dict_carries_attempts(self):
        from src.tools.base import ToolResult

        self.assertEqual(1, ToolResult(result={}).to_dict()["attempts"])


if __name__ == "__main__":
    unittest.main()
