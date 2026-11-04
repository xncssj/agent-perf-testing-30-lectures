"""审计与内容最小化、依赖调用上下文的单元测试（006 R06/R03）。

- 审计摘要：合成敏感内容进、结构化摘要出，任何摘要函数不回显原文；
- 依赖上下文：traceparent 与 run_id 可逆（trace-id 即 run_id），工具线程
  经上下文快照读到传播头，无上下文时不伪造身份。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.harness.audit import (  # noqa: E402
    AUDIT_RULES_VERSION,
    audit_event,
    length_bucket,
    summarize_answer,
    summarize_input,
    summarize_tool_request,
    summarize_tool_result,
)
from src.harness.budget import Budget  # noqa: E402
from src.harness.contracts import RunContext, ToolCall  # noqa: E402
from src.harness.registry import ToolRegistry  # noqa: E402
from src.harness.tool_executor import ToolExecutor  # noqa: E402
from src.harness.trace_context import (  # noqa: E402
    dependency_headers,
    reset_dependency_context,
    set_dependency_context,
    traceparent,
)

SENSITIVE = "13812345678"
ID_CARD = "110101199001011234"


class AuditSummaryTest(unittest.TestCase):
    def test_length_buckets(self):
        self.assertEqual(length_bucket(""), "empty")
        self.assertEqual(length_bucket("a" * 50), "≤50")
        self.assertEqual(length_bucket("a" * 51), "51-200")
        self.assertEqual(length_bucket("a" * 801), ">800")
        self.assertEqual(length_bucket(None), "empty")

    def test_summaries_never_echo_raw_text(self):
        query = f"我的手机号 {SENSITIVE}，身份证 {ID_CARD}，帮我查订单"
        for summary in (
            summarize_input(query, intent="order_query"),
            summarize_tool_request("query_order", {"order_id": ID_CARD}),
            summarize_tool_result("query_order", {"found": True},
                                  None, None, 12.0),
            summarize_tool_result("query_order", {}, "数据库连接串 jdbc:..",
                                  "exception", 12.0),
            summarize_answer(f"请联系 {SENSITIVE}", "answered",
                             {"blocked": True, "violations": [1]}),
            audit_event("r1", "s1", "input_received", "accepted",
                        summary=summarize_input(query)),
        ):
            # 逐个摘要断言（文本形态的 dict/json 均不含原文）
            dumped = repr(summary)
            self.assertNotIn(SENSITIVE, dumped)
            self.assertNotIn(ID_CARD, dumped)
            self.assertNotIn("jdbc:", dumped)

    def test_tool_result_summary_states(self):
        ok = summarize_tool_result("query_order", {"found": True}, None, None, 3.2)
        self.assertEqual(ok["status"], "ok")
        self.assertTrue(ok["found"])
        bad = summarize_tool_result("query_order", {}, "x", "timeout", 0)
        self.assertEqual(bad["status"], "error")
        self.assertEqual(bad["error_kind"], "timeout")
        self.assertIsNone(bad["found"])

    def test_audit_event_carries_rule_version(self):
        ev = audit_event("run-1", "step-1", "guardrail_decision", "blocked")
        self.assertEqual(ev["rule_version"], AUDIT_RULES_VERSION)
        self.assertEqual(ev["run_id"], "run-1")
        self.assertEqual(ev["decision"], "blocked")


class TraceparentTest(unittest.TestCase):
    def test_trace_id_is_run_id(self):
        run_id = "a" * 32
        tp = traceparent(run_id, "b" * 32)
        self.assertTrue(tp.startswith(f"00-{run_id}-"))
        self.assertTrue(tp.endswith("-01"))
        span = tp.split("-")[2]
        self.assertEqual(len(span), 16)

    def test_headers_absent_without_context(self):
        self.assertEqual(dependency_headers(), {})

    def test_headers_present_with_context(self):
        token = set_dependency_context("a" * 32, "c" * 32)
        try:
            headers = dependency_headers()
            self.assertEqual(headers["x-agent-run-id"], "a" * 32)
            self.assertEqual(headers["x-agent-tool-call-id"], "c" * 32)
            self.assertIn("traceparent", headers)
        finally:
            reset_dependency_context(token)


class _HeaderRecordingTool:
    """在工作线程内记录依赖上下文头的桩工具（execute 返回 ToolResult 形状）。"""

    name = "query_order"
    description = "test"
    timeout_ms = 2000.0
    retry = 0

    seen: list[dict] = []

    def execute(self, **kwargs):
        _HeaderRecordingTool.seen.append(dependency_headers())
        result = {"found": True, "order": {"order_id": kwargs.get("order_id", "")}}
        return type("ToolResult", (), {
            "result": result, "latency_ms": 0.5, "error": None,
            "timed_out": False, "attempts": 1,
        })()


class ExecutorPropagationTest(unittest.TestCase):
    """006 R03：执行器把依赖上下文快照带入工作线程，线程内可读传播头。"""

    def test_tool_thread_reads_dependency_headers(self):
        _HeaderRecordingTool.seen = []
        registry = ToolRegistry()
        registry.register(_HeaderRecordingTool())
        executor = ToolExecutor(registry, tool_workers=1, tool_queue_size=1)

        class _Recorder:
            def emit_tool_call(self, *a, **k):
                pass

            def emit_tool_result(self, *a, **k):
                pass

            def record_rejection(self, *a, **k):
                pass

        ctx = RunContext(task_id="T", run_id="a" * 32)
        call = ToolCall(name="query_order", arguments={"order_id": "O1"})
        obs = asyncio.run(executor.run(call, ctx, Budget(2, 2, 10),
                                       _Recorder(), parent_span_id="span-1"))
        self.assertIsNone(obs.error)
        self.assertEqual(len(_HeaderRecordingTool.seen), 1)
        headers = _HeaderRecordingTool.seen[0]
        self.assertEqual(headers.get("x-agent-run-id"), "a" * 32)
        self.assertEqual(headers.get("x-agent-tool-call-id"), call.id)
        # 执行结束后上下文已复位，不残留到其他调用
        self.assertEqual(dependency_headers(), {})


if __name__ == "__main__":
    unittest.main()
