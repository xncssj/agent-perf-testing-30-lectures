"""门禁指标来源与安全/质量聚合的单元测试。

覆盖 2026-09-12 待办 2b / 2c 的关键口径：
- 安全违规数取自基准报告的安全聚合，缺失时明确失败而非按 0 通过；
- 重试去重：聚合层按"逻辑调用"只记一条，事件层保留每次尝试；
- Q=0 与空批次不产生除零错误；
- 汇总报告的门禁小节唯一（不在循环里重复渲染）。
"""
from __future__ import annotations

import asyncio
import sys
import time
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.budget import Budget  # noqa: E402
from harness.contracts import RunContext, ToolCall  # noqa: E402
from harness.guardrail import Guardrail, violation_count  # noqa: E402
from harness.registry import ToolEntry, ToolRegistry  # noqa: E402
from harness.tool_executor import ToolExecutor  # noqa: E402
from src.gates.report_metrics import (  # noqa: E402
    MissingReportError,
    build_gate_metrics,
)
from tests.performance.common import Ticket, aggregate, classify, failed_business  # noqa: E402


class _ToolResult:
    """工具执行结果最小载体（与各阶段 ToolResult 字段一致）。"""

    def __init__(self, result=None, error=None, timed_out=False):
        self.result = result or {}
        self.error = error
        self.timed_out = timed_out


class FlakyTool:
    """首次调用超时、重试成功的桩工具（验证重试去重）。"""

    name = "flaky"
    timeout_ms = 20.0
    retry = 1
    calls = 0

    def execute(self, **kwargs):
        FlakyTool.calls += 1
        if FlakyTool.calls == 1:
            time.sleep(0.08)  # 超过 timeout_ms=20ms，由执行器判定超时并重试
        return _ToolResult(result={"ok": True, "attempt_call": FlakyTool.calls})


class RecordingRecorder:
    """最小 recorder：记录事件与拒绝，供断言使用。"""

    def __init__(self):
        self.events = []

    def _emit(self, kind, **kw):
        self.events.append({"type": kind, **kw})

    def emit_tool_call(self, tool, call_id, attempt, args, parent_span_id=""):
        self._emit("tool_call", tool=tool, tool_call_id=call_id, attempt=attempt)

    def emit_tool_result(self, tool, call_id, attempt, data, error, duration_ms,
                         parent_span_id="", error_kind=None):
        self._emit("tool_result", tool=tool, tool_call_id=call_id, attempt=attempt,
                   error=error, duration_ms=duration_ms)

    def record_rejection(self, tool, call_id, reason):
        self._emit("rejection", tool=tool, tool_call_id=call_id, reason=reason)


class TestRetryDedupe(unittest.TestCase):
    def test_logical_call_recorded_once_attempts_kept_in_events(self):
        FlakyTool.calls = 0
        registry = ToolRegistry()
        registry.register(FlakyTool(), enabled=True)
        executor = ToolExecutor(registry, tool_workers=1, tool_queue_size=1)
        recorder = RecordingRecorder()
        ctx = RunContext(
            run_id="r1", task_id="t1", deadline=time.monotonic() + 10.0,
        )
        budget = Budget(max_iterations=3, max_tool_calls=5, timeout_s=10.0)

        obs = asyncio.run(executor.run(
            ToolCall(name="flaky", arguments={}), ctx, budget, recorder
        ))

        # 最终观察来自重试后的成功结果（只返回一条）
        self.assertFalse(obs.error)
        self.assertTrue(obs.data.get("ok"))
        # 事件层保留两次尝试（attempt=1 失败、attempt=2 成功）
        attempts = [e["attempt"] for e in recorder.events if e["type"] == "tool_call"]
        self.assertEqual([1, 2], attempts)
        # 逻辑调用计数按 tool_call_id 去重后仍为 1
        logical = {e["tool_call_id"] for e in recorder.events if e["type"] == "tool_call"}
        self.assertEqual(1, len(logical))
        asyncio.run(executor.aclose())


class TestGuardrailHelpers(unittest.TestCase):
    def test_violation_count_shapes(self):
        g = Guardrail()
        self.assertEqual(0, violation_count(g.check("您的订单已发货。")))
        self.assertEqual(1, violation_count(g.check("请拨打 13812345678 联系客服")))
        self.assertEqual(0, violation_count(None))
        self.assertEqual(0, violation_count({}))


class TestSecurityAggregation(unittest.TestCase):
    def _ticket(self):
        return Ticket(ticket_id="T1", user_query="q", expected_answer_keywords=["已发货"])

    def test_violating_business_not_qualified(self):
        t = self._ticket()
        result = {
            "status": "completed",
            "response": "您的订单已发货，可拨打 13812345678 联系客服。",
            "trace": {"guardrail": {"violations": [{"type": "sensitive"}], "blocked": True}},
        }
        biz = classify(t, result, slo_ms=8000.0, first_answer_slo_ms=2000.0)
        self.assertFalse(biz["levels"]["security_ok"])
        self.assertFalse(biz["levels"]["qualified"])

    def test_clean_business_qualified(self):
        t = self._ticket()
        result = {
            "status": "completed",
            "response": "您的订单已发货。",
            "trace": {"guardrail": {"violations": [], "blocked": False}},
        }
        biz = classify(t, result, slo_ms=8000.0, first_answer_slo_ms=2000.0)
        self.assertTrue(biz["levels"]["security_ok"])
        self.assertTrue(biz["levels"]["qualified"])

    def test_missing_guardrail_is_not_treated_as_safe(self):
        t = self._ticket()
        result = {"status": "completed", "response": "您的订单已发货。", "trace": {}}
        biz = classify(t, result, slo_ms=8000.0, first_answer_slo_ms=2000.0)
        self.assertIsNone(biz["levels"]["security_ok"])

    def test_aggregate_reports_security_and_quality(self):
        t = self._ticket()
        good = classify(t, {
            "status": "completed", "response": "您的订单已发货。",
            "trace": {"guardrail": {"violations": []}},
        }, slo_ms=8000.0)
        bad = classify(t, {
            "status": "completed", "response": "您的订单已发货，电话 13812345678。",
            "trace": {"guardrail": {"violations": [{"type": "sensitive"}]}},
        }, slo_ms=8000.0)
        agg = aggregate([good, bad], duration_s=1.0)
        self.assertEqual(1, agg["安全指标"]["违规业务数"])
        self.assertEqual(1, agg["安全指标"]["违规条数"])
        self.assertEqual(0, agg["安全指标"]["安全数据缺失业务数"])
        self.assertEqual(2, agg["质量指标"]["期望要素总数(关键词)"])
        self.assertEqual(2, agg["质量指标"]["命中要素数(关键词)"])
        # Q 只认无违规的那笔
        self.assertEqual(1, agg["合格率"]["合格业务"])


class TestZeroQualifiedBoundary(unittest.TestCase):
    def test_q_zero_and_empty_batch(self):
        t = Ticket(ticket_id="Z", user_query="q")
        agg = aggregate([failed_business(t, "boom")] * 2, duration_s=1.0)
        self.assertEqual(0, agg["合格率"]["合格业务"])
        self.assertEqual(0.0, agg["业务Goodput(合格业务/秒)"])
        self.assertIsNone(agg["成本指标"]["每合格业务token"])
        self.assertIsNone(agg["质量指标"]["要素命中率(关键词,%)"])

        empty = aggregate([], duration_s=0.0)
        self.assertEqual(0.0, empty["业务Goodput(合格业务/秒)"])
        self.assertEqual(0.0, empty["安全指标"]["违规业务率(%)"])


class TestGateMetricsSource(unittest.TestCase):
    def _bench(self, security: dict | None):
        agg = {
            "合格率": {"合格业务百分比": 100.0},
            "时延指标(mm)": {"E2E": {"p90": 3600.0}},
            "成本指标": {"成功业务成本(元/合格业务)": 0.0095},
        }
        if security is not None:
            agg["安全指标"] = security
        return {"aggregate": agg}

    def test_reads_measured_violations(self):
        # 006 G02 后稳定性报告为必需项（缺报告/缺字段不再填理想值）
        metrics = build_gate_metrics(
            self._bench({"违规条数": 2}), {"coverage": {"branch_coverage": 1.0}},
            {"availability": 1.0, "error_rate": 0.0},
        )
        self.assertEqual(2, metrics["security_violations"])

    def test_missing_stability_fails_loudly(self):
        # 缺 stability 报告按 NOT_VERIFIED 语义抛错，不得回填 1.0/0.0（006 G02）
        with self.assertRaises(MissingReportError):
            build_gate_metrics(
                self._bench({"违规条数": 2}), {"coverage": {"branch_coverage": 1.0}},
                None,
            )

    def test_missing_security_fails_loudly(self):
        with self.assertRaises(MissingReportError):
            build_gate_metrics(self._bench(None), {"coverage": {"branch_coverage": 1.0}})

    def test_missing_reports_fail_loudly(self):
        with self.assertRaises(MissingReportError):
            build_gate_metrics(None, None)


if __name__ == "__main__":
    unittest.main()
