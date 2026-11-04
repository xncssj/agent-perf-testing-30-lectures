"""超管运行器测试（阶段二 v2）：终态映射、span 关联、降级与预算终止。"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent  # 案例根：共享 harness 包所在目录
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.budget import Budget  # noqa: E402
from harness.contracts import ModelUsage, RunInput, ToolCall  # noqa: E402
from harness.contracts import AnswerChunk, Decision, RunContext  # noqa: E402
from harness.providers import ProviderError  # noqa: E402
from harness.tool_executor import ToolExecutor  # noqa: E402
from harness.registry import ToolRegistry  # noqa: E402

from src.agent.prompt_manager import PromptManager  # noqa: E402
from src.mas.mock_provider import MockProvider  # noqa: E402
from src.mas.provider import (  # noqa: E402
    AgentTaskResult,
    BaseMultiAgentProvider,
    PlanResult,
    ValidationVerdict,
)
from src.mas.supervisor import SupervisorEngine  # noqa: E402
from src.mas.worker import Workforce  # noqa: E402
from src.models.database import Database, load_seeds  # noqa: E402
from src.tools.knowledge_tool import KnowledgeTool  # noqa: E402
from src.tools.logistics_tool import LogisticsTool  # noqa: E402
from src.tools.order_tool import OrderTool  # noqa: E402

SHARED_DATA_DIR = PROJECT_ROOT.parent.parent / "testing" / "test-data"
ORDER_ID = "2024010100001"


def make_settings(**kw) -> SimpleNamespace:
    base = dict(
        model_name="mock", mock_seed=42,
        mock_plan_latency_ms=1, mock_plan_jitter_ms=1,
        mock_agent_latency_ms=1, mock_agent_jitter_ms=1,
        mock_validation_latency_ms=1, mock_token_latency_ms=1,
        max_iterations=10, max_tool_calls=5, request_timeout=30,
        tool_workers=2, tool_queue_size=4,
        parallel_limit=4, agent_timeout_ms=5000, max_retries=1,
        retry_backoff_ms=1, breaker_threshold=5, breaker_window_ms=1000,
        degradation_enabled=True, temperature=0, max_tokens=100,
        fault_enabled=False, fault_target="", fault_mode="",
        fault_latency_ms=0, fault_limit=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class ScriptedProvider(BaseMultiAgentProvider):
    """脚本化 Multi-Agent Provider：按配置返回规划/决策/校验/答复。

    - plan_agents=None 时默认派发 order+logistics；空列表表示澄清类规划；
    - fail_tools=True 时产出空参数调用（参数校验拒绝 → Worker 执行失败）；
    - answer_mode="facts" 按聚合事实生成答复（复用 Mock 答复逻辑），
      "generic" 输出固定文本；fail_stream_at=N 在第 N 个片段后中断答复流。
    """

    evidence_kind = "mock"
    model = "scripted"

    def __init__(self, plan_agents=None, fail_tools=False,
                 answer_mode="facts", answer_chunks=("答", "案"),
                 fail_stream_at=None, plan_delay_s=0.0):
        self.plan_agents = plan_agents
        self.fail_tools = fail_tools
        self.answer_mode = answer_mode
        self.answer_chunks = list(answer_chunks)
        self.fail_stream_at = fail_stream_at
        self.plan_delay_s = plan_delay_s
        self.plan_calls = 0
        self._mock = MockProvider(make_settings())

    async def plan(self, query, context) -> PlanResult:
        import asyncio

        self.plan_calls += 1
        if self.plan_delay_s:
            await asyncio.sleep(self.plan_delay_s)
        agents = (list(self.plan_agents) if self.plan_agents is not None
                  else ["order_agent", "logistics_agent"])
        return PlanResult(intent="logistics_query", agents=agents,
                          model=self.model, latency_ms=1.0,
                          usage=ModelUsage(prompt_tokens=10, completion_tokens=5,
                                           total_tokens=15, source="reported"))

    async def agent_task(self, agent, query, context) -> AgentTaskResult:
        tool = {"order_agent": "query_order", "logistics_agent": "query_logistics",
                "knowledge_agent": "search_knowledge"}.get(agent, "")
        args = {} if self.fail_tools else {"order_id": ORDER_ID}
        return AgentTaskResult(
            tool=tool or None, args=args, model=self.model, latency_ms=1.0,
            usage=ModelUsage(prompt_tokens=8, completion_tokens=2,
                             total_tokens=10, source="reported"),
        )

    async def validation(self, query, facts, context) -> ValidationVerdict:
        return ValidationVerdict(status="complete", model=self.model,
                                 latency_ms=1.0,
                                 usage=ModelUsage(source="estimated"))

    async def stream_answer(self, messages, context) -> AsyncIterator[AnswerChunk]:
        if self.answer_mode == "facts":
            chunks = [self._mock._answer(messages)]
        else:
            chunks = self.answer_chunks
        for i, ch in enumerate(chunks):
            if self.fail_stream_at is not None and i >= self.fail_stream_at:
                raise ProviderError("模拟上游流中断")
            yield AnswerChunk(content=ch)
        yield AnswerChunk(content="", usage=ModelUsage(prompt_tokens=20,
                                                       completion_tokens=4,
                                                       total_tokens=24,
                                                       source="reported"),
                          finish=True)

    async def aclose(self):
        pass


class TestSupervisor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
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
        return SupervisorEngine(provider, Workforce(provider, self.executor),
                                self.executor, self.pm, make_settings(**kw))

    def _run(self, runner, query, task_id="T1", batch_id="B1") -> list[dict]:
        async def go():
            events = []
            async for ev in runner.run_stream(
                RunInput(query=query, task_id=task_id, batch_id=batch_id)
            ):
                events.append(ev)
            return events

        return asyncio.run(go())

    # ---- 正常链路：身份/span/并行/校验/事实 ----
    def test_happy_path_spans_and_facts(self):
        runner = self._runner(ScriptedProvider(answer_mode="facts"))
        events = self._run(runner, f"订单{ORDER_ID}到哪了", task_id="T001")
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "thinking")
        self.assertEqual(types[-1], "done")
        self.assertIn("agent_invoke", types)
        self.assertIn("validation", types)
        done = events[-1]
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["stop_reason"], "answered")
        self.assertIn("上海分拨中心", done["response"])
        # 身份与 span：Worker 继承根 run_id，span_id 独立
        trace = done["trace"]
        self.assertEqual(done["task_id"], "T001")
        self.assertEqual(done["batch_id"], "B1")
        for a in trace["agents"]:
            self.assertEqual(a["parent_span_id"], done["run_id"])
            self.assertTrue(a["span_id"])
        # 并行效率 > 1（两个 Worker 并行执行）
        self.assertGreater(trace["fanout"]["efficiency"], 1.0)
        # 校验 Agent 恒有执行记录
        self.assertEqual(trace["validation"]["status"], "complete")
        self.assertIn("validation_agent", trace["plan_agents"])
        # seq 严格递增
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    # ---- 出口 ----
    def test_clarify_exit(self):
        provider = ScriptedProvider(plan_agents=[])
        runner = self._runner(provider)
        ev = self._run(runner, "那个东西到哪了")
        self.assertEqual(ev[-1]["status"], "completed")
        self.assertEqual(ev[-1]["stop_reason"], "clarification")
        self.assertEqual(ev[-1]["metrics"]["evidence_kind"], "template")

    def test_complaint_handoff_no_false_claim(self):
        class ComplaintProvider(ScriptedProvider):
            async def plan(self, query, context):
                return PlanResult(intent="complaint", agents=[], model=self.model,
                                  latency_ms=1.0,
                                  usage=ModelUsage(source="estimated"))

        ev = self._run(self._runner(ComplaintProvider()), "我要投诉")
        self.assertEqual(ev[-1]["stop_reason"], "handoff_requested")
        self.assertIn("联系人工客服", ev[-1]["response"])
        self.assertNotIn("已将您的问题记录并转交", ev[-1]["response"])

    def test_not_found_exit(self):
        class NotFoundProvider(ScriptedProvider):
            async def agent_task(self, agent, query, context):
                # 查询执行成功但订单不存在（found=false 不是错误）
                return AgentTaskResult(
                    tool={"order_agent": "query_order",
                          "logistics_agent": "query_logistics"}.get(agent),
                    args={"order_id": "9999999999999"}, model=self.model,
                    latency_ms=1.0, usage=ModelUsage(source="estimated"),
                )

        ev = self._run(self._runner(NotFoundProvider()), "订单9999999999999到哪了")
        self.assertEqual(ev[-1]["stop_reason"], "not_found")
        self.assertIn("没有查询到", ev[-1]["response"])
        self.assertEqual(ev[-1]["status"], "completed")

    # ---- 降级与失败终态 ----
    def test_degraded_answer_when_degradation_enabled(self):
        """工具失败 + 降级开启：如实生成降级答复，trace 标注降级。"""
        provider = ScriptedProvider(fail_tools=True, answer_mode="facts")
        runner = self._runner(provider)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "completed")
        self.assertIn("不可用", ev[-1]["response"])
        self.assertTrue(ev[-1]["trace"]["degraded"])
        self.assertTrue(ev[-1]["trace"]["degraded_agents"])

    def test_tool_failed_when_degradation_disabled(self):
        """降级关闭：未解决的执行错误 → failed/tool_failed。"""
        provider = ScriptedProvider(fail_tools=True)
        runner = self._runner(provider, degradation_enabled=False)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "failed")
        self.assertEqual(ev[-1]["stop_reason"], "tool_failed")

    def test_budget_exhausted_across_workers(self):
        """根预算原子分配：两个 Worker 竞争 1 次工具尝试，超预算即终态。"""
        runner = self._runner(ScriptedProvider(), max_tool_calls=1)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "budget_exhausted")
        self.assertEqual(ev[-1]["stop_reason"], "max_tool_calls")

    def test_provider_error_terminal(self):
        class BoomProvider(ScriptedProvider):
            async def plan(self, query, context):
                raise ProviderError("规划连接中断")

        ev = self._run(self._runner(BoomProvider()), "查询")
        self.assertEqual(ev[-1]["status"], "failed")
        self.assertEqual(ev[-1]["stop_reason"], "provider_error")

    def test_stream_failure_keeps_partial_and_fails(self):
        provider = ScriptedProvider(answer_mode="generic",
                                    answer_chunks=("部分", "答"),
                                    fail_stream_at=1)
        ev = self._run(self._runner(provider), "查询")
        self.assertEqual(ev[-1]["status"], "failed")
        self.assertEqual(ev[-1]["stop_reason"], "provider_error")
        self.assertEqual(ev[-1]["response"], "部分")

    def test_empty_answer_terminal(self):
        provider = ScriptedProvider(answer_mode="generic", answer_chunks=[])
        ev = self._run(self._runner(provider), "查询")
        self.assertEqual(ev[-1]["stop_reason"], "empty_answer")

    def test_deadline_terminates(self):
        runner = self._runner(ScriptedProvider(plan_delay_s=5.0),
                              request_timeout=0.15)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "timed_out")
        self.assertEqual(ev[-1]["stop_reason"], "request_deadline")

    def test_fault_injection_degrades(self):
        """故障注入：指定 Worker 注入超时 → 重试耗尽 → 降级答复。"""
        provider = ScriptedProvider()
        runner = self._runner(provider, fault_enabled=True,
                              fault_target="order_agent", fault_mode="timeout",
                              fault_latency_ms=8000, agent_timeout_ms=50,
                              max_retries=0, degradation_enabled=True)
        ev = self._run(runner, "查询")
        self.assertEqual(ev[-1]["status"], "completed")
        self.assertTrue(ev[-1]["trace"]["degraded"])
        statuses = {a["name"]: a["status"] for a in ev[-1]["trace"]["agents"]}
        self.assertEqual(statuses.get("order_agent"), "timeout")


if __name__ == "__main__":
    unittest.main()
