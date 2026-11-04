"""工具执行器单元测试：校验、超时、重试、过载与迟到结果（H04/H05/H11 部分）。"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.harness.budget import Budget, BudgetExceeded
from src.harness.contracts import RunContext, ToolCall
from src.harness.tool_executor import ToolExecutor
from src.tools.base import BaseTool
from src.tools.registry import ToolRegistry
from src.tools.params import QueryOrderArgs


class StubRecorder:
    """记录事件的桩记录器。"""

    def __init__(self):
        self.calls = []
        self.results = []
        self.rejections = []

    def emit_tool_call(self, tool, tool_call_id, attempt, args, parent_span_id=""):
        self.calls.append((tool, tool_call_id, attempt, args))

    def emit_tool_result(self, tool, tool_call_id, attempt, data, error, duration_ms,
                         parent_span_id="", error_kind=None):
        self.results.append((tool, tool_call_id, attempt, data, error))

    def record_rejection(self, name, tool_call_id, reason):
        self.rejections.append((name, tool_call_id, reason))


class EchoTool(BaseTool):
    name = "echo"
    description = "回显参数"
    args_model = None

    def _run(self, **kwargs):
        return {"found": True, **kwargs}


class FailTool(BaseTool):
    name = "fail"
    description = "总是失败"
    args_model = None

    def _run(self, **kwargs):
        raise RuntimeError("boom")


class SlowTool(BaseTool):
    """可控慢工具：每次执行睡 sleep_s 秒。"""

    name = "slow"
    description = "慢工具"
    args_model = None

    def __init__(self, sleep_s: float, config=None):
        super().__init__(config or {"timeout": 10, "retry": 0})
        self.sleep_s = sleep_s

    def _run(self, **kwargs):
        time.sleep(self.sleep_s)
        return {"found": True}


class FlakyTimeoutTool(BaseTool):
    """前 fail_times 次执行超时（sleep 超过工具 timeout），随后成功。"""

    name = "flaky"
    description = "前几次超时后成功"
    args_model = None

    def __init__(self, fail_times: int, exec_sleep_s: float, config=None):
        super().__init__(config or {"timeout": 0.2, "retry": 3})
        self.fail_times = fail_times
        self.exec_sleep_s = exec_sleep_s
        self.attempts = 0

    def _run(self, **kwargs):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            time.sleep(self.exec_sleep_s)
        return {"found": True, "attempt": self.attempts}


def make_ctx(timeout_s=30.0) -> RunContext:
    import time as _t

    return RunContext(task_id="T", run_id="R", deadline=_t.monotonic() + timeout_s)


def run_coro(coro):
    return asyncio.run(coro)


class TestToolExecutor(unittest.TestCase):
    def _executor(self, registry, workers=2, queue_size=2):
        return ToolExecutor(registry, tool_workers=workers, tool_queue_size=queue_size)

    # ---- 校验拒绝（H04）----
    def test_unknown_tool_rejected_without_execution(self):
        registry = ToolRegistry()
        executor = self._executor(registry)
        rec = StubRecorder()
        obs = run_coro(executor.run(
            ToolCall(name="nope", arguments={}), make_ctx(), Budget(5, 5, 30), rec
        ))
        self.assertTrue(obs.rejected)
        self.assertEqual(obs.error_kind, "unknown_tool")
        self.assertEqual(rec.calls, [], "未知工具不得产生 tool_call 事件")
        self.assertEqual(rec.results, [])
        self.assertEqual(len(rec.rejections), 1)

    def test_invalid_arguments_rejected_no_retry_no_budget(self):
        registry = ToolRegistry()

        class OrderStub(BaseTool):
            name = "query_order"
            description = "订单"
            args_model = QueryOrderArgs

            def _run(self, **kwargs):
                raise AssertionError("不应执行到底层工具")

        registry.register(OrderStub())
        executor = self._executor(registry)
        rec = StubRecorder()
        budget = Budget(5, 5, 30)
        obs = run_coro(executor.run(
            ToolCall(name="query_order", arguments={"order_id": ""}),
            make_ctx(), budget, rec,
        ))
        self.assertTrue(obs.rejected)
        self.assertEqual(obs.error_kind, "invalid_arguments")
        self.assertEqual(rec.calls, [], "参数拒绝不得产生实际执行的 tool_call 事件")
        self.assertEqual(budget.tool_attempts_used, 0, "参数被拒绝不计实际执行")

    def test_disabled_tool_rejected(self):
        registry = ToolRegistry()
        registry.register(EchoTool(), enabled=False)
        executor = self._executor(registry)
        rec = StubRecorder()
        obs = run_coro(executor.run(
            ToolCall(name="echo", arguments={}), make_ctx(), Budget(5, 5, 30), rec
        ))
        self.assertEqual(obs.error_kind, "disabled_tool")
        self.assertEqual(rec.calls, [])

    # ---- 正常执行与事件配对 ----
    def test_success_emits_paired_events(self):
        registry = ToolRegistry()
        registry.register(EchoTool(config={"timeout": 1, "retry": 0}))
        executor = self._executor(registry)
        rec = StubRecorder()
        call = ToolCall(name="echo", arguments={"x": 1})
        obs = run_coro(executor.run(call, make_ctx(), Budget(5, 5, 30), rec))
        self.assertIsNone(obs.error)
        self.assertTrue(obs.data.get("found"))
        self.assertEqual(rec.calls[0][0:2], ("echo", call.id))
        self.assertEqual(rec.results[0][0:2], ("echo", call.id))
        self.assertEqual(rec.results[0][3].get("found"), True)

    # ---- 超时与重试（H05）----
    def test_timeout_retries_then_succeeds(self):
        registry = ToolRegistry()
        tool = FlakyTimeoutTool(fail_times=2, exec_sleep_s=0.6,
                                config={"timeout": 0.2, "retry": 3})
        registry.register(tool)
        # 失败尝试的线程会滞留至自然结束，池容量需容纳全部滞留线程
        executor = ToolExecutor(registry, tool_workers=3, tool_queue_size=1)
        rec = StubRecorder()
        budget = Budget(5, 5, 30)
        obs = run_coro(executor.run(
            ToolCall(name="flaky", arguments={}), make_ctx(timeout_s=30), budget, rec
        ))
        self.assertIsNone(obs.error, "重试后应成功")
        self.assertEqual(tool.attempts, 3, "两次超时 + 第三次成功")
        self.assertEqual(budget.tool_attempts_used, 3, "重试计入实际执行尝试")
        attempts = [c[2] for c in rec.calls]
        self.assertEqual(attempts, [1, 2, 3], "各 attempt 有记录")

    def test_timeout_budget_stops_retry(self):
        """重试前重新检查剩余预算：预算耗尽即终止，尝试总数不超预算。"""
        registry = ToolRegistry()
        tool = FlakyTimeoutTool(fail_times=10, exec_sleep_s=0.3,
                                config={"timeout": 0.1, "retry": 5})
        registry.register(tool)
        executor = ToolExecutor(registry, tool_workers=3, tool_queue_size=1)
        rec = StubRecorder()
        budget = Budget(5, 2, 30)
        with self.assertRaises(BudgetExceeded):
            run_coro(executor.run(
                ToolCall(name="flaky", arguments={}), make_ctx(timeout_s=30),
                budget, rec,
            ))
        self.assertEqual(budget.tool_attempts_used, 2)

    def test_tool_exception_not_retried(self):
        """底层工具异常不属于暂时性故障，不自动重试。"""
        registry = ToolRegistry()
        registry.register(FailTool(config={"timeout": 1, "retry": 3}))
        executor = self._executor(registry)
        rec = StubRecorder()
        budget = Budget(5, 5, 30)
        obs = run_coro(executor.run(
            ToolCall(name="fail", arguments={}), make_ctx(), budget, rec
        ))
        self.assertIn("boom", obs.error)
        self.assertEqual(budget.tool_attempts_used, 1, "异常只尝试一次")

    # ---- 过载（H11）----
    def test_overload_fails_explicitly(self):
        """池满时明确返回过载失败，不无限排队。"""
        registry = ToolRegistry()
        blocked = threading.Event()
        release = threading.Event()

        class BlockTool(BaseTool):
            name = "block"
            description = "阻塞"
            args_model = None

            def _run(self, **kwargs):
                blocked.set()
                release.wait(timeout=10)
                return {"found": True}

        registry.register(BlockTool(config={"timeout": 10, "retry": 0}))
        executor = ToolExecutor(registry, tool_workers=1, tool_queue_size=0)

        async def scenario():
            rec1, rec2 = StubRecorder(), StubRecorder()
            ctx = make_ctx(30)
            task1 = asyncio.create_task(executor.run(
                ToolCall(name="block", arguments={}), ctx, Budget(5, 5, 30), rec1
            ))
            await asyncio.to_thread(blocked.wait, 5)
            # 工作槽被 task1 占用（workers=1, queue=0）
            obs2 = await executor.run(
                ToolCall(name="block", arguments={}), ctx, Budget(5, 5, 30), rec2
            )
            release.set()
            obs1 = await task1
            return obs1, obs2

        obs1, obs2 = run_coro(scenario())
        self.assertIsNone(obs1.error)
        self.assertEqual(obs2.error_kind, "overloaded", "过载必须明确失败")

    # ---- 工作槽释放（H10 部分）----
    def test_slot_released_after_late_tool_finish(self):
        """等待超时后线程继续执行，槽位在线程自然结束时释放。"""
        registry = ToolRegistry()
        tool = SlowTool(sleep_s=0.6, config={"timeout": 0.15, "retry": 0})
        registry.register(tool)
        executor = ToolExecutor(registry, tool_workers=1, tool_queue_size=0)
        rec = StubRecorder()

        async def scenario():
            ctx = make_ctx(30)
            obs = await executor.run(
                ToolCall(name="slow", arguments={}), ctx, Budget(5, 5, 30), rec
            )
            self.assertTrue(obs.timed_out)
            self.assertEqual(executor.in_flight, 1, "线程未结束前不能释放工作槽")
            await asyncio.sleep(0.9)  # 等待线程体自然结束
            return executor.in_flight

        in_flight = run_coro(scenario())
        self.assertEqual(in_flight, 0, "迟到结果不得占用工作槽")


if __name__ == "__main__":
    unittest.main()
