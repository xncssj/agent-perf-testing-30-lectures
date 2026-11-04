"""阶段二契约与预算测试：Worker 身份映射、根预算原子分配（H16 契约样例）。"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent  # 案例根：共享 harness 包所在目录
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.budget import Budget, BudgetExceeded, DeadlineExceeded
from harness.contracts import new_id


class TestWorkerIdentityContract(unittest.TestCase):
    """H16 契约样例：Worker 样例映射相同身份/终态，不冒充阶段二运行验收。"""

    def test_agent_call_info_identity_fields(self):
        from src.mas.worker import AgentCallInfo

        info = AgentCallInfo(
            name="order_agent", role="订单查询", status="success",
            tool="query_order", args={"order_id": "O1"},
            span_id=new_id(), parent_span_id="root_run_1",
            task_id="T1", run_id="root_run_1", batch_id="B1",
        )
        d = info.to_dict()
        # 身份继承：task/batch/根 run 与根运行一致，parent_span = 根 run_id
        self.assertEqual(d["task_id"], "T1")
        self.assertEqual(d["batch_id"], "B1")
        self.assertEqual(d["run_id"], "root_run_1")
        self.assertEqual(d["parent_span_id"], "root_run_1")
        self.assertTrue(d["span_id"])
        self.assertNotEqual(d["span_id"], d["parent_span_id"])


class TestBudgetAtomicAllocation(unittest.TestCase):
    """根预算原子分配：并发 Worker 分配不超发，耗尽即拒绝。"""

    def test_atomic_allocation_under_concurrency(self):
        budget = Budget(max_iterations=10, max_tool_calls=3, timeout_s=30)

        async def go():
            async def allocate():
                await asyncio.sleep(0)
                return budget.allocate_tool_attempt()

            return await asyncio.gather(*[allocate() for _ in range(6)],
                                        return_exceptions=True)

        results = asyncio.run(go())
        ok = sorted(r for r in results if isinstance(r, int))
        rejected = [r for r in results if isinstance(r, BudgetExceeded)]
        self.assertEqual(ok, [1, 2, 3], "原子分配不超发")
        self.assertEqual(len(rejected), 3, "耗尽后拒绝")
        self.assertEqual(budget.tool_attempts_used, 3)

    def test_deadline_blocks_allocation(self):
        budget = Budget(max_iterations=10, max_tool_calls=5, timeout_s=0.03)
        time.sleep(0.06)
        with self.assertRaises(DeadlineExceeded):
            budget.allocate_tool_attempt()

    def test_counters_snapshot(self):
        budget = Budget(max_iterations=4, max_tool_calls=2, timeout_s=5)
        budget.begin_iteration()
        budget.allocate_tool_attempt()
        c = budget.counters()
        self.assertEqual(c["iterations_used"], 1)
        self.assertEqual(c["tool_attempts_used"], 1)
        self.assertEqual(c["max_tool_calls"], 2)


if __name__ == "__main__":
    unittest.main()
