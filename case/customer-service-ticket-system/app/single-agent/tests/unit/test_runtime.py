"""运行器单元测试：调用配对、终态集合、预算终止、取消与并发隔离（H01/H02/H06/H07/H12 部分）。

使用脚本化 Provider 与真实业务工具（临时数据库 + 共享数据快照）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.prompt_manager import PromptManager
from src.harness.contracts import (
    AnswerChunk,
    Decision,
    Message,
    ModelUsage,
    RunInput,
    ToolCall,
)
from src.harness.runtime import HarnessRunner
from src.harness.tool_executor import ToolExecutor
from src.llm.base import BaseProvider, InvalidDecisionError, ProviderError
from src.llm.mock_provider import MockProvider
from src.models.database import Database, load_seeds
from src.tools.knowledge_tool import KnowledgeTool
from src.tools.logistics_tool import LogisticsTool
from src.tools.order_tool import OrderTool
from src.tools.registry import ToolRegistry

# 共享基准数据集单一事实源：案例根 testing/test-data（见其 MANIFEST.md）
SHARED_DATA_DIR = PROJECT_ROOT.parents[1] / "testing" / "test-data"
ORDER_ID = "2024010100001"


def make_settings(**kw) -> SimpleNamespace:
    base = dict(
        model_name="mock", mock_seed=42, mock_plan_latency_ms=1,
        mock_plan_jitter_ms=1, mock_token_latency_ms=1,
        max_iterations=10, max_tool_calls=5, request_timeout=30,
        tool_workers=2, tool_queue_size=4,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class ScriptedProvider(BaseProvider):
    """脚本化 Provider：按预置序列返回决策；支持挂起与异常。"""

    evidence_kind = "mock"

    def __init__(self, script=None, answer_chunks=("答", "复"), answer_usage=None):
        self.script = list(script or [])
        self.answer_chunks = list(answer_chunks)
        self.answer_usage = answer_usage
        self.decide_calls = 0
        self.model = "scripted"

    async def decide(self, messages, tools, context) -> Decision:
        self.decide_calls += 1
        item = self.script.pop(0) if self.script else Decision(action="finalize")
        if isinstance(item, str) and item == "hang":
            await asyncio.sleep(60)
        if isinstance(item, Exception):
            raise item
        return item

    async def stream_answer(self, messages, context) -> AsyncIterator[AnswerChunk]:
        for ch in self.answer_chunks:
            await asyncio.sleep(0.005)
            yield AnswerChunk(content=ch)
        yield AnswerChunk(content="", usage=self.answer_usage, finish=True)

    async def aclose(self):
        pass


def call_tools(*specs) -> Decision:
    """specs: (name, args) 列表 -> call_tools 决策。"""
    return Decision(
        action="call_tools",
        tool_calls=[ToolCall(name=n, arguments=a) for n, a in specs],
        usage=ModelUsage(prompt_tokens=10, completion_tokens=2, total_tokens=12,
                         source="reported"),
    )


class TestRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        orders, logistics, knowledge = load_seeds(SHARED_DATA_DIR)
        self.db.seed_loads(orders, logistics, knowledge)
        self.registry = ToolRegistry()
        self.registry.register(OrderTool(self.db, {"timeout": 2, "retry": 0}))
        self.registry.register(LogisticsTool(self.db, {"timeout": 2, "retry": 0}))
        self.registry.register(KnowledgeTool(self.db, {"timeout": 2, "retry": 0}))
        self.executor = ToolExecutor(self.registry, tool_workers=2, tool_queue_size=4)
        self.pm = PromptManager(PROJECT_ROOT / "src" / "prompts")

    def tearDown(self):
        asyncio.run(self.executor.aclose())
        self.tmp.cleanup()

    def _runner(self, provider, **kw):
        return HarnessRunner(provider, self.executor, make_settings(**kw), self.pm)

    def _run(self, runner, query, task_id="T1", batch_id="B1") -> dict:
        async def go():
            events = []
            async for ev in runner.run_stream(
                RunInput(query=query, task_id=task_id, batch_id=batch_id)
            ):
                events.append(ev)
            return events

        return asyncio.run(go())

    # ---- H01：订单→物流→答复，调用/结果 ID 配对 ----
    def test_h01_full_flow_pairs_and_facts(self):
        provider = MockProvider(make_settings())
        events = self._run(self._runner(provider),
                           f"我的订单{ORDER_ID}到哪了？", task_id="T001")
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "thinking")
        self.assertEqual(types[-1], "done")
        # tool_call 与 tool_result 按 tool_call_id 成对出现，顺序正确
        pending = {}
        for e in events:
            if e["type"] == "tool_call":
                pending[e["tool_call_id"]] = e["tool"]
            elif e["type"] == "tool_result":
                self.assertIn(e["tool_call_id"], pending, "结果必须有对应调用 ID")
                self.assertEqual(pending.pop(e["tool_call_id"]), e["tool"])
        self.assertEqual(list(pending), [])
        # 工具顺序：先订单后物流
        tool_seq = [e["tool"] for e in events if e["type"] == "tool_call"]
        self.assertEqual(tool_seq, ["query_order", "query_logistics"])
        done = events[-1]
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["stop_reason"], "answered")
        self.assertIn("已发货", done["response"])
        self.assertIn("上海分拨中心", done["response"])
        # 身份：评测传入的 task_id 固定，run_id 服务端生成
        self.assertEqual(done["task_id"], "T001")
        self.assertNotEqual(done["run_id"], done["task_id"])
        self.assertEqual(done["batch_id"], "B1")
        # 事件公共字段
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs), "seq 严格递增")
        self.assertEqual(len(set(seqs)), len(seqs), "seq 不重复")

    # ---- H07：澄清 / 查询无结果 / 人工处理建议出口 ----
    def test_h07_exits(self):
        runner = self._runner(MockProvider(make_settings()))
        # 模糊工单 → 澄清
        ev = self._run(runner, "那个东西到哪了")
        self.assertEqual(ev[-1]["stop_reason"], "clarification")
        self.assertIn("订单号", ev[-1]["response"])
        # 不存在的订单 → not_found（found=false 不伪装成异常）
        ev = self._run(runner, "订单9999999999999到哪了")
        self.assertEqual(ev[-1]["stop_reason"], "not_found")
        self.assertIn("没有查询到", ev[-1]["response"])
        self.assertEqual(ev[-1]["status"], "completed")
        # 投诉 → 人工处理建议：只建议行为，不声称已转交
        ev = self._run(runner, "我要投诉这个平台")
        self.assertEqual(ev[-1]["stop_reason"], "handoff_requested")
        self.assertIn("联系人工客服", ev[-1]["response"])
        self.assertNotIn("已将您的问题记录并转交", ev[-1]["response"])
        self.assertNotIn("转交人工客服，", ev[-1]["response"])

    # ---- H02：同一轮多个工具，超预算不启动下一项 ----
    def test_h02_multi_tool_budget_stop(self):
        provider = ScriptedProvider(script=[
            call_tools(("query_order", {"order_id": ORDER_ID}),
                       ("query_logistics", {"order_id": ORDER_ID})),
        ])
        runner = self._runner(provider, max_tool_calls=1)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "budget_exhausted")
        self.assertEqual(ev[-1]["stop_reason"], "max_tool_calls")
        executed = [e["tool"] for e in ev if e["type"] == "tool_call"]
        self.assertEqual(executed, ["query_order"], "超预算时不启动下一项")

    def test_h02_multi_tool_executes_all_in_budget(self):
        provider = ScriptedProvider(script=[
            call_tools(("query_order", {"order_id": ORDER_ID}),
                       ("query_logistics", {"order_id": ORDER_ID})),
        ])
        runner = self._runner(provider)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "completed")
        executed = [e["tool"] for e in ev if e["type"] == "tool_call"]
        self.assertEqual(executed, ["query_order", "query_logistics"])

    # ---- H06：无限选工具 / 不结束的模型 ----
    def test_h06_runaway_iterations(self):
        script = [call_tools(("search_knowledge", {"query": "退货"})) for _ in range(20)]
        runner = self._runner(ScriptedProvider(script=script), max_iterations=3)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "budget_exhausted")
        self.assertEqual(ev[-1]["stop_reason"], "max_iterations")
        # 不生成成功兜底答复
        self.assertEqual(ev[-1]["response"], "")

    def test_h06_deadline_terminates_hang(self):
        runner = self._runner(ScriptedProvider(script=["hang"]), request_timeout=0.2)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "timed_out")
        self.assertEqual(ev[-1]["stop_reason"], "request_deadline")

    def test_last_round_finalize_allows_generation(self):
        """最后一轮已 finalize 时允许完成最终生成。"""
        runner = self._runner(
            ScriptedProvider(script=[
                call_tools(("query_order", {"order_id": ORDER_ID})),
                Decision(action="finalize"),
            ]),
            max_iterations=2,
        )
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "completed")
        self.assertEqual(ev[-1]["response"], "答复")

    # ---- 失败终态 ----
    def test_invalid_decision_terminal(self):
        runner = self._runner(ScriptedProvider(script=[InvalidDecisionError("混合动作")]))
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "failed")
        self.assertEqual(ev[-1]["stop_reason"], "invalid_decision")

    def test_provider_error_terminal(self):
        runner = self._runner(ScriptedProvider(script=[ProviderError("连接中断")]))
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["stop_reason"], "provider_error")
        self.assertEqual(ev[-1]["response"], "")

    def test_empty_answer_terminal(self):
        provider = ScriptedProvider(
            script=[
                call_tools(("query_order", {"order_id": ORDER_ID})),
                Decision(action="finalize"),
            ],
            answer_chunks=[],
        )
        runner = self._runner(provider)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["stop_reason"], "empty_answer")
        self.assertEqual(ev[-1]["status"], "failed")

    def test_unresolved_tool_error_not_disguised_as_not_found(self):
        """未解决的执行错误不能转换成“查询无结果”。"""
        def broken_run(**kwargs):
            raise RuntimeError("数据库不可用")

        self.registry._entries["query_order"].tool._run = broken_run
        provider = ScriptedProvider(script=[
            call_tools(("query_order", {"order_id": ORDER_ID})),
        ])
        runner = self._runner(provider)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "failed")
        self.assertEqual(ev[-1]["stop_reason"], "tool_failed")

    # ---- H12：片段与 token 拆分 ----
    def test_h12_single_chunk_metrics(self):
        provider = ScriptedProvider(
            script=[
                call_tools(("query_order", {"order_id": ORDER_ID})),
                Decision(action="finalize"),
            ],
            answer_chunks=("只有一段",),
        )
        runner = self._runner(provider)
        ev = self._run(runner, "查询")
        m = ev[-1]["metrics"]
        self.assertEqual(m["chunk_intervals_ms"], [], "单片段无间隔列表")
        self.assertIsNotNone(m["first_answer_ms"])
        self.assertIsNone(m["ttft_ms"], "无 token 级证据时 TTFT 为空")
        self.assertIsNone(m["tpot_ms"])
        self.assertEqual(m["evidence_kind"], "mock")

    def test_h12_usage_missing_stays_null(self):
        provider = ScriptedProvider(answer_usage=None)
        # 决策用量缺失
        provider.script = [Decision(action="finalize", usage=ModelUsage(source="missing"))]
        runner = self._runner(provider)
        ev = self._run(runner, "查询")
        m = ev[-1]["metrics"]
        self.assertEqual(m["usage_source"], "missing")
        self.assertIsNone(m["total_tokens"])
        self.assertIsNone(m["token_count"])
        # 空指标可序列化
        json.dumps(ev[-1])

    def test_usage_mixed_source(self):
        script = [
            call_tools(("query_order", {"order_id": ORDER_ID})),
        ]
        provider = ScriptedProvider(script=script,
                                    answer_usage=ModelUsage(
                                        prompt_tokens=5, completion_tokens=3,
                                        total_tokens=8, source="missing"))
        runner = self._runner(provider)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["metrics"]["usage_source"], "mixed")

    # ---- 取消（H10 运行器侧）----
    def test_cancellation_stops_and_releases(self):
        def slow_run(**kwargs):
            time.sleep(0.3)
            return {"found": True}

        self.registry._entries["query_order"].tool._run = slow_run
        provider = ScriptedProvider(script=[
            Decision(action="call_tools",
                     tool_calls=[ToolCall(
                         name="query_order",
                         arguments={"order_id": ORDER_ID})]),
            Decision(action="call_tools",
                     tool_calls=[ToolCall(name="search_knowledge",
                                          arguments={"query": "退货"})]),
        ])
        runner = self._runner(provider)

        async def go():
            task = asyncio.create_task(runner.run(RunInput(query="q")))
            await asyncio.sleep(0.05)
            self.assertGreaterEqual(provider.decide_calls, 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            # 事件循环存活期间等待迟到线程自然收尾并释放工作槽
            for _ in range(40):
                if self.executor.in_flight == 0:
                    break
                await asyncio.sleep(0.05)
            return provider.decide_calls, self.executor.in_flight

        decide_calls, in_flight = asyncio.run(go())
        self.assertEqual(decide_calls, 1, "取消后不再发起后续模型调用")
        self.assertEqual(in_flight, 0, "取消后工作槽最终释放")

    # ---- 并发隔离（H11 部分）----
    def test_concurrent_runs_unique_ids(self):
        provider = MockProvider(make_settings())
        runner = self._runner(provider)

        async def go():
            results = await asyncio.gather(*[
                runner.run(RunInput(query=f"订单{ORDER_ID}到哪了", task_id=f"T{i}"))
                for i in range(5)
            ])
            return results

        results = asyncio.run(go())
        run_ids = {r.run_id for r in results}
        self.assertEqual(len(run_ids), 5, "run_id 不重复")
        for r in results:
            self.assertEqual(r.status, "completed")
            self.assertIn("上海分拨中心", r.response, "历史不串扰")


if __name__ == "__main__":
    unittest.main()
