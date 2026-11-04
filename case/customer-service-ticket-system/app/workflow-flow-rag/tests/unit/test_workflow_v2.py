"""Workflow v2 运行器测试：节点边界事件、身份、终态映射与 RAG 溯源。"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.contracts import RunInput  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402


def make_service(**overrides) -> WorkflowService:
    import os

    os.environ.setdefault("FLOW_DB_PATH", "/tmp/harness_m3/unit.db")
    settings = Settings(**{k: v for k, v in overrides.items() if k != "_"})
    for k, v in overrides.items():
        setattr(settings, k, v)
    return WorkflowService(settings)


class TestWorkflowV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = make_service()

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.service.aclose())

    def _run(self, query, task_id="T1", batch_id="B1"):
        async def go():
            events = []
            async for ev in self.service._run_stream(
                RunInput(query=query, task_id=task_id, batch_id=batch_id)
            ):
                events.append(ev)
            return events

        return asyncio.run(go())

    def test_happy_path_node_events_and_facts(self):
        events = self._run("我的订单2024010100001到哪了？", task_id="T001")
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "thinking")
        self.assertEqual(types[-1], "done")
        # 节点边界事件：node 事件携带 node_id
        node_events = [e for e in events if e["type"] == "node"]
        self.assertTrue(node_events, "应有节点边界事件")
        self.assertIn("intent_recognition", {e["node_id"] for e in node_events})
        self.assertIn("data_retrieval", {e["node_id"] for e in node_events})
        # 工具事件（经共享执行器）
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        done = events[-1]
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["stop_reason"], "answered")
        self.assertIn("上海分拨中心", done["response"])
        self.assertEqual(done["task_id"], "T001")
        self.assertEqual(done["batch_id"], "B1")
        # trace：路径/分支/节点执行记录（含 attempts）
        trace = done["trace"]
        self.assertEqual(trace["path"], ["intent_recognition", "data_retrieval",
                                         "response_generation", "confidence_check"])
        self.assertEqual(trace["branch"], "auto_reply")
        for ne in trace["node_executions"]:
            self.assertIn("attempts", ne)
        # seq 严格递增
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))

    def test_clarify_exit(self):
        ev = self._run("那个东西到哪了")
        self.assertEqual(ev[-1]["status"], "completed")
        self.assertEqual(ev[-1]["stop_reason"], "clarification")
        self.assertIn("订单号", ev[-1]["response"])

    def test_complaint_handoff_honest_wording(self):
        ev = self._run("我要投诉这个平台")
        self.assertEqual(ev[-1]["stop_reason"], "handoff_requested")
        self.assertIn("联系人工客服", ev[-1]["response"])
        self.assertNotIn("已为您转接", ev[-1]["response"])

    def test_rag_provenance_and_citations(self):
        ev = self._run("退货政策是什么")
        trace = ev[-1]["trace"]
        self.assertEqual(trace["branch"], "auto_reply")
        retrieval = trace["retrieval"]
        self.assertTrue(retrieval.get("doc_ids"), "应记录命中文档 ID")
        self.assertTrue(retrieval.get("index_version"), "应记录索引版本")
        self.assertEqual(retrieval.get("corpus"), "customer-service-kb")
        self.assertIn("knowledge_retrieval", trace["path"])
        self.assertTrue(trace.get("citations"), "应记录生成引用（答复依据文档）")
        for cid in trace["citations"]:
            self.assertIn(cid, retrieval["doc_ids"])

    def test_deadline_maps_to_timed_out(self):
        svc = make_service()
        try:
            # 覆盖意图延迟（默认 550ms）> 极小运行期限
            svc.settings.intent_latency_ms = 5000
            svc.settings.intent_jitter_ms = 0
            svc.settings.request_timeout = 0.2

            async def go():
                events = []
                async for ev in svc._run_stream(
                    RunInput(query="订单2024010100001到哪了", task_id="T-D")
                ):
                    events.append(ev)
                return events

            ev = asyncio.run(go())
            self.assertEqual(ev[-1]["status"], "timed_out")
            self.assertEqual(ev[-1]["stop_reason"], "request_deadline")
        finally:
            asyncio.run(svc.aclose())

    def test_loop_budget_maps_to_budget_exhausted(self):
        """自定义环形图：循环预算耗尽映射 budget_exhausted/max_node_executions。"""
        from src.workflow.engine import (
            Edge,
            LoopBudgetExceeded,
            NodeDef,
            WorkflowExecutor,
        )

        async def go():
            events = []
            scope_hit = {}
            nodes = [
                NodeDef(id="a", type="intent_recognition", name="A"),
                NodeDef(id="b", type="data_retrieval", name="B"),
            ]
            edges = [
                Edge("a", "b", condition=None),
                Edge("b", "a", condition=None),  # 环
            ]
            runner = lambda node, context: {"ok": True}  # noqa: E731
            executor = WorkflowExecutor(
                nodes=nodes, edges=edges, start_node="a",
                node_runner=runner, settings=self.service.settings,
            )

            def check_deadline():
                pass

            def record_node(node_id, node_type, latency, status, attempts):
                scope_hit[node_id] = True

            context_seed = {"_scope": {
                "recorder": None, "ctx": None, "budget": None,
                "check_deadline": check_deadline, "record_node": record_node,
                "tool_executor": None,
            }}
            result = executor.execute("q", scope=context_seed["_scope"])
            events.append(result)
            return events

        ev = asyncio.run(go())
        result = ev[0]
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertIn("循环预算", result.get("error") or "")

    def test_in_process_v1_compat_still_works(self):
        """v1 兼容入口：process() 返回执行器结果（status=completed）。"""
        result = self.service.process("我的订单2024010100001到哪了？")
        self.assertEqual(result["status"], "completed")
        self.assertIn("上海分拨中心", result["response"])


if __name__ == "__main__":
    unittest.main()
