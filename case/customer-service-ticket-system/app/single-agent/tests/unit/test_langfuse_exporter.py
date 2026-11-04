"""Langfuse 导出器单元测试：载荷映射、鉴权、失败不影响业务。

使用 httpx MockTransport 验证 Ingestion 请求体，不发起真实网络请求。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.harness.langfuse_exporter import LangfuseConfig, LangfuseExporter


def make_config(enabled=True, **kw) -> LangfuseConfig:
    base = dict(
        enabled=enabled, host="http://langfuse.example.com",
        public_key="pk-lf-test", secret_key="sk-lf-test", timeout_s=2.0,
    )
    base.update(kw)
    return LangfuseConfig(**base)


def sample_events() -> list[dict]:
    return [
        {"type": "thinking", "run_id": "R1", "task_id": "T1", "batch_id": "B1",
         "seq": 1, "ts_ms": 0.0, "ts_wall": 1000.0, "content": "开始"},
        {"type": "tool_call", "run_id": "R1", "seq": 2, "ts_ms": 5.0,
         "ts_wall": 1001.0, "tool": "query_order", "tool_call_id": "c1",
         "attempt": 1, "args": {"order_id": "O1"}},
        {"type": "tool_result", "run_id": "R1", "seq": 3, "ts_ms": 25.0,
         "ts_wall": 1001.5, "tool": "query_order", "tool_call_id": "c1",
         "attempt": 1, "data": {"found": True}, "error": None, "duration_ms": 20.0},
        {"type": "tool_result", "run_id": "R1", "seq": 4, "ts_ms": 30.0,
         "ts_wall": 1002.0, "tool": "query_logistics", "tool_call_id": "c2",
         "attempt": 2, "data": {}, "error": "等待超时", "duration_ms": 5.0},
        {"type": "model_call", "run_id": "R1", "seq": 5, "ts_ms": 1.0,
         "ts_wall": 1000.5, "call_kind": "decision", "model": "mock",
         "latency_ms": 12.0,
         "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                   "total_tokens": 12, "source": "estimated"}},
        {"type": "done", "run_id": "R1", "task_id": "T1", "batch_id": "B1",
         "seq": 6, "ts_ms": 50.0, "ts_wall": 1003.0, "status": "completed",
         "stop_reason": "answered", "response": "您的订单已发货",
         "metrics": {"evidence_kind": "mock", "usage_source": "estimated",
                     "total_tokens": 20, "e2e_ms": 50.0}},
    ]


def stage_events_multi_agent() -> list[dict]:
    """阶段二样例：子 Agent 调度、聚合校验与带编排 trace 的终态。"""
    return [
        {"type": "agent_invoke", "run_id": "R2", "task_id": "T2", "batch_id": "B2",
         "seq": 1, "ts_ms": 20.0, "ts_wall": 3000.0, "agent": "order_agent",
         "status": "success", "span_id": "sp-1", "attempts": 1},
        {"type": "agent_invoke", "run_id": "R2", "task_id": "T2", "batch_id": "B2",
         "seq": 2, "ts_ms": 21.0, "ts_wall": 3000.5, "agent": "order_agent",
         "status": "success", "span_id": "sp-1", "attempts": 1},
        {"type": "agent_invoke", "run_id": "R2", "task_id": "T2", "batch_id": "B2",
         "seq": 2, "ts_ms": 30.0, "ts_wall": 3001.0, "agent": "logistics_agent",
         "status": "skipped_breaker", "span_id": "sp-2", "attempts": 3},
        {"type": "validation", "run_id": "R2", "task_id": "T2", "batch_id": "B2",
         "seq": 3, "ts_ms": 40.0, "ts_wall": 3002.0,
         "validation": {"status": "ok", "missing": ["logistics"],
                        "latency_ms": 5.0, "tokens": 8, "fallback": False}},
        {"type": "done", "run_id": "R2", "task_id": "T2", "batch_id": "B2",
         "seq": 4, "ts_ms": 60.0, "ts_wall": 3003.0, "status": "completed",
         "stop_reason": "answered", "response": "已为您查询订单",
         "metrics": {"evidence_kind": "mock", "usage_source": "estimated",
                     "total_tokens": 30, "e2e_ms": 60.0,
                     "first_answer_ms": 45.0, "answer_chunks": 6,
                     "chunk_intervals_ms": [8.0, 9.0, 10.0],
                     "guardrail": {"violations": [], "blocked": False}},
         "trace": {"intent": "order_query",
                   "plan_agents": ["order_agent", "validation_agent"],
                   "fanout": {"round_wall_ms": 30.0, "serial_sum_ms": 45.0,
                              "efficiency": 1.5, "agents": 2},
                   "validation": {"status": "ok", "missing": ["logistics"],
                                  "latency_ms": 5.0, "tokens": 8,
                                  "fallback": False},
                   "degraded": True, "degraded_agents": ["logistics_agent"]}},
    ]


def stage_events_workflow() -> list[dict]:
    """阶段三样例：节点执行边界与带路径/检索摘要的终态。"""
    return [
        {"type": "node", "run_id": "R3", "task_id": "T3", "batch_id": "B3",
         "seq": 1, "ts_ms": 10.0, "ts_wall": 2000.010,
         "node_id": "intent_recognition", "node_type": "llm",
         "latency_ms": 10.0, "status": "completed", "attempts": 1},
        {"type": "node", "run_id": "R3", "task_id": "T3", "batch_id": "B3",
         "seq": 2, "ts_ms": 30.0, "ts_wall": 2000.030,
         "node_id": "data_retrieval", "node_type": "tool",
         "latency_ms": 20.0, "status": "failed", "attempts": 2},
        {"type": "done", "run_id": "R3", "task_id": "T3", "batch_id": "B3",
         "seq": 3, "ts_ms": 90.0, "ts_wall": 2000.090, "status": "completed",
         "stop_reason": "answered", "response": "已为您检索知识库",
         "metrics": {"evidence_kind": "mock", "usage_source": "estimated",
                     "total_tokens": 40, "e2e_ms": 90.0,
                     "first_answer_ms": 70.0, "answer_chunks": 8,
                     "chunk_intervals_ms": [5.0]},
         "trace": {"intent": "policy_query",
                   "path": ["intent_recognition", "data_retrieval",
                            "knowledge_retrieval", "response_generation"],
                   "branch": "auto_reply", "confidence": 0.92,
                   "degraded": False,
                   "retrieval": {"hits": [{"doc_id": "kb-1"}, {"doc_id": "kb-2"}],
                                 "doc_ids": ["kb-1", "kb-2"], "top_k": 3,
                                 "latency_ms": 4.5, "corpus": 42,
                                 "index_version": "bm25-v1"},
                   "citations": [{"doc_id": "kb-1"}, {"doc_id": "kb-2"}],
                   "guardrail": {"violations": [], "blocked": False},
                   "node_executions": [{"node_id": "intent_recognition"}] * 6,
                   "node_exec_errors": []}},
    ]


class TestLangfuseExporter(unittest.TestCase):
    def _exporter(self, captured: dict, enabled=True, **kw) -> LangfuseExporter:
        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["auth"] = request.headers.get("Authorization", "")
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok"})

        client = httpx.AsyncClient(
            base_url="http://langfuse.example.com",
            transport=httpx.MockTransport(handler),
            auth=("pk-lf-test", "sk-lf-test"),
        )
        return LangfuseExporter(make_config(enabled=enabled, **kw), client=client)

    def test_disabled_exporter_is_noop(self):
        captured: dict = {}
        exporter = self._exporter(captured, enabled=False)
        for ev in sample_events():
            exporter.observe(ev)
        result = asyncio.run(exporter.flush_now("R1", query="q"))
        self.assertIsNone(result, "禁用时不缓冲不上报")

    def test_config_validation_requires_keys(self):
        # 校验在导出器构造时触发（启用但缺少密钥必须启动失败）
        with self.assertRaises(ValueError):
            LangfuseExporter(make_config(public_key="", secret_key=""))

    def test_flush_builds_trace_generation_span(self):
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events():
            exporter.observe(ev)
        summary = asyncio.run(exporter.flush_now("R1", query="我的订单到哪了"))
        self.assertIsNotNone(summary)
        self.assertEqual(captured["path"], "/api/public/ingestion")
        # Basic 认证（pk:sk）
        self.assertTrue(captured["auth"].startswith("Basic "))
        batch = captured["payload"]["batch"]
        types = [i["type"] for i in batch]
        self.assertIn("trace-create", types)
        self.assertIn("generation-create", types)
        self.assertEqual(types.count("span-create"), 2, "每次工具尝试一个 span")
        # 006 R06：整份载荷不出现查询/答复原文
        self.assertNotIn("我的订单到哪了", json.dumps(captured["payload"], ensure_ascii=False))
        # v4 信封格式：事件字段在 body 内，且每条事件都带 ISO timestamp
        for item in batch:
            self.assertIn("timestamp", item)
            self.assertIsInstance(item["body"], dict)
        trace = next(i for i in batch if i["type"] == "trace-create")
        self.assertEqual(trace["id"], "R1", "Trace ID = run_id")
        # 006 R06：input/output 为审计摘要，不再导出查询与答复原文
        self.assertEqual(trace["body"]["input"],
                         {"length_bucket": "≤50", "intent": None,
                          "accepted": True})
        self.assertEqual(trace["body"]["output"]["stop_reason"], "answered")
        self.assertEqual(trace["body"]["metadata"]["status"], "completed")
        self.assertEqual(trace["body"]["metadata"]["task_id"], "T1")
        # 006 R03：无真实 conversation_id 时 sessionId 留空，不用 batch_id 冒充
        self.assertIsNone(trace["body"]["sessionId"])
        gen = next(i for i in batch if i["type"] == "generation-create")
        self.assertEqual(gen["body"]["traceId"], "R1")
        self.assertEqual(gen["body"]["usage"]["total"], 12)
        self.assertEqual(gen["body"]["model"], "mock")
        spans = [i for i in batch if i["type"] == "span-create"]
        levels = {s["body"]["level"] for s in spans}
        self.assertIn("ERROR", levels, "失败尝试 span 标记 ERROR")

    def test_generation_uses_start_wall_when_present(self):
        """R3/P5：model_call 携带 start_wall 时 generation 按真实区间换算。

        调用开始 999.0、耗时 12ms → [999.0, 999.012]，不再用发射时刻
        ts_wall=1000.5 起算（旧口径区间整体后移一个 latency）。
        """
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events():
            if ev["type"] == "model_call":
                ev = dict(ev, start_wall=999.0)
            exporter.observe(ev)
        summary = asyncio.run(exporter.flush_now("R1", query="q"))
        self.assertIsNotNone(summary)
        batch = captured["payload"]["batch"]
        gen = next(i for i in batch if i["type"] == "generation-create")
        started = datetime.fromtimestamp(999.0, tz=timezone.utc).isoformat()
        ended = datetime.fromtimestamp(999.012, tz=timezone.utc).isoformat()
        self.assertEqual(gen["body"]["startTime"], started)
        self.assertEqual(gen["body"]["endTime"], ended)

    def test_generation_falls_back_to_emit_wall_without_start_wall(self):
        """旧调用方未传 start_wall 时保持旧口径（发射时刻起算），行为兼容。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events():
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R1", query="q"))
        batch = captured["payload"]["batch"]
        gen = next(i for i in batch if i["type"] == "generation-create")
        started = datetime.fromtimestamp(1000.5, tz=timezone.utc).isoformat()
        self.assertEqual(gen["body"]["startTime"], started,
                         "无 start_wall 时回退事件 ts_wall，既有行为不变")

    def test_final_answer_generation_carries_real_latency(self):
        """R3/P5：final_answer 携带答案段真实时长与首片段起点。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events():
            exporter.observe(ev)
        exporter.observe({
            "type": "model_call", "run_id": "R1", "seq": 7, "ts_ms": 45.0,
            "ts_wall": 1002.8, "call_kind": "final_answer", "model": "",
            "latency_ms": 150.0, "start_wall": 1001.3,
            "usage": {"prompt_tokens": 5, "completion_tokens": 7,
                      "total_tokens": 12, "source": "estimated"},
        })
        asyncio.run(exporter.flush_now("R1", query="q"))
        batch = captured["payload"]["batch"]
        gens = [i for i in batch if i["type"] == "generation-create"]
        final = next(g for g in gens if g["body"]["name"] == "final_answer")
        started = datetime.fromtimestamp(1001.3, tz=timezone.utc).isoformat()
        ended = datetime.fromtimestamp(1001.45, tz=timezone.utc).isoformat()
        self.assertEqual(final["body"]["startTime"], started)
        self.assertEqual(final["body"]["endTime"], ended,
                         "final_answer 区间 = 首片段起点 + 答案段真实时长")

    def test_stage_spans_multi_agent(self):
        """阶段二：子 Agent 调度与校验映射为 span，编排摘要并入 metadata。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in stage_events_multi_agent():
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R2", query="查订单"))
        batch = captured["payload"]["batch"]
        spans = {i["body"]["name"]: i["body"] for i in batch
                 if i["type"] == "span-create"}
        self.assertEqual(
            {"agent:order_agent", "agent:logistics_agent", "validation"},
            set(spans), "子 Agent 与校验各一个 span",
        )
        agent_spans = [i for i in batch if i["type"] == "span-create"
                       and i["body"]["name"] == "agent:order_agent"]
        self.assertEqual(
            len(agent_spans), 1, "同一次调度（同 span_id）的重复事件只映射一个 span",
        )
        self.assertEqual(spans["agent:order_agent"]["level"], "DEFAULT")
        self.assertEqual(
            spans["agent:logistics_agent"]["level"], "ERROR",
            "熔断跳过属失败态，span 标 ERROR",
        )
        self.assertEqual(spans["agent:logistics_agent"]["metadata"]["attempts"], 3)
        self.assertEqual(spans["validation"]["metadata"]["status"], "ok")
        trace = next(i for i in batch if i["type"] == "trace-create")
        meta = trace["body"]["metadata"]
        self.assertEqual(meta["plan_agents"], ["order_agent", "validation_agent"])
        self.assertEqual(meta["fanout"]["efficiency"], 1.5)
        self.assertEqual(meta["validation"]["missing"], ["logistics"])
        self.assertTrue(meta["degraded"])
        self.assertEqual(meta["degraded_agents"], ["logistics_agent"])
        self.assertEqual(meta["first_answer_ms"], 45.0)
        self.assertEqual(meta["chunk_intervals_ms"], [8.0, 9.0, 10.0])
        self.assertEqual(
            meta["guardrail"], {"blocked": False, "violations": 0},
            "护栏结果摘要为 blocked 计数形式",
        )

    def test_node_spans_and_workflow_metadata(self):
        """阶段三：节点边界映射为 span，路径/检索摘要并入 metadata。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in stage_events_workflow():
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R3", query="退货政策是什么"))
        batch = captured["payload"]["batch"]
        spans = {i["body"]["name"]: i["body"] for i in batch
                 if i["type"] == "span-create"}
        self.assertEqual(
            {"node:intent_recognition", "node:data_retrieval"}, set(spans),
            "每个节点执行边界一个 span",
        )
        self.assertEqual(spans["node:intent_recognition"]["level"], "DEFAULT")
        self.assertEqual(
            spans["node:data_retrieval"]["level"], "WARNING",
            "非 completed 节点标 WARNING",
        )
        # 节点 span 的 startTime 由 ts_wall - latency_ms 反推
        start = datetime.fromisoformat(spans["node:data_retrieval"]["startTime"])
        end = datetime.fromisoformat(spans["node:data_retrieval"]["endTime"])
        self.assertAlmostEqual((end - start).total_seconds(), 0.020, places=3)
        trace = next(i for i in batch if i["type"] == "trace-create")
        meta = trace["body"]["metadata"]
        self.assertEqual(meta["path"][0], "intent_recognition")
        self.assertEqual(meta["branch"], "auto_reply")
        self.assertEqual(meta["confidence"], 0.92)
        self.assertEqual(
            meta["retrieval"],
            {"top_k": 3, "latency_ms": 4.5, "corpus": 42,
             "index_version": "bm25-v1", "hits": 2},
            "检索摘要取标量字段，hits 折算为条数",
        )
        self.assertEqual(meta["citations"], 2, "引用列表折算为条数")
        self.assertEqual(meta["node_executions"], 6)
        self.assertNotIn("node_exec_errors", meta, "空摘要字段不占位")
        self.assertFalse(meta["degraded"])

    def test_single_agent_trace_has_no_stage_keys(self):
        """阶段一（无 trace 摘要）的终态不产生阶段专属键。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events():
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R1", query="q"))
        trace = next(i for i in captured["payload"]["batch"]
                     if i["type"] == "trace-create")
        meta = trace["body"]["metadata"]
        for key in ("path", "branch", "plan_agents", "fanout", "validation",
                    "retrieval", "guardrail", "first_answer_ms"):
            self.assertNotIn(key, meta, f"阶段一终态不应出现 {key}")

    def test_stage_tag_added_when_configured(self):
        """配置 stage 后 Trace 标签附带 stage:<阶段>，便于按阶段筛选。"""
        captured: dict = {}
        exporter = self._exporter(captured, stage="s01")
        for ev in sample_events():
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R1", query="q"))
        trace = next(i for i in captured["payload"]["batch"]
                     if i["type"] == "trace-create")
        self.assertIn("stage:s01", trace["body"]["tags"])

    def test_no_stage_tag_by_default(self):
        """未配置 stage 时不产生阶段标签。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events():
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R1", query="q"))
        trace = next(i for i in captured["payload"]["batch"]
                     if i["type"] == "trace-create")
        stage_tags = [t for t in trace["body"]["tags"] if t.startswith("stage:")]
        self.assertEqual(stage_tags, [])

    def test_multi_status_with_errors_is_not_silent(self):
        """207 多状态响应：被拒事件必须显式返回失败，不得当作上报成功。"""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(207, json={
                "successes": [{"id": "s1", "status": 201}],
                "errors": [
                    {"id": "e1", "status": 400, "error": "Event type not accepted"},
                    {"id": "e2", "status": 400, "error": "Invalid input"},
                ],
            })

        client = httpx.AsyncClient(
            base_url="http://langfuse.example.com",
            transport=httpx.MockTransport(handler),
            auth=("pk-lf-test", "sk-lf-test"),
        )
        exporter = LangfuseExporter(make_config(), client=client)
        for ev in sample_events():
            exporter.observe(ev)
        summary = asyncio.run(exporter.flush_now("R1", query="q"))
        # 4 条事件中 1 条成功 2 条被拒（样例含 trace+generation+2 span=4 条；
        # MockTransport 固定返回 2 个错误）：部分成功时报告成功条数
        self.assertIsNotNone(summary)
        self.assertEqual(summary["status"], 207)

    def test_all_rejected_reports_failure(self):
        """全部被拒（如 v4 events_only 拒绝旧版事件）必须返回失败而非静默。"""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(207, json={
                "successes": [],
                "errors": [{"id": "R1", "status": 400,
                            "error": "Event type not accepted"}],
            })

        client = httpx.AsyncClient(
            base_url="http://langfuse.example.com",
            transport=httpx.MockTransport(handler),
            auth=("pk-lf-test", "sk-lf-test"),
        )
        exporter = LangfuseExporter(make_config(), client=client)
        for ev in sample_events():
            exporter.observe(ev)
        summary = asyncio.run(exporter.flush_now("R1", query="q"))
        self.assertIsNone(summary, "全部被拒时不得返回成功")

    def test_flush_failure_does_not_raise(self):
        def handler(request):
            return httpx.Response(500, text="server error")

        client = httpx.AsyncClient(
            base_url="http://langfuse.example.com",
            transport=httpx.MockTransport(handler),
        )
        exporter = LangfuseExporter(make_config(), client=client)
        for ev in sample_events():
            exporter.observe(ev)
        result = asyncio.run(exporter.flush_now("R1", query="q"))
        self.assertIsNone(result, "上报失败只记录日志，不向业务抛异常")

    def test_observe_never_raises_on_bad_event(self):
        captured: dict = {}
        exporter = self._exporter(captured)
        exporter.observe({"type": "tool_call"})  # 缺 run_id：忽略
        exporter.observe(None)
        exporter.observe({"run_id": "R2", "type": "tool_result"})  # 无配对开始
        summary = asyncio.run(exporter.flush_now("R2", query="q"))
        self.assertIsNotNone(summary)

    def test_partial_flush_on_disconnect(self):
        """消费端断连时以部分证据上报（取消终态标注）。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events()[:2]:  # 只到 tool_call，无 done
            exporter.observe(ev)
        exporter.observe({
            "run_id": "R1", "task_id": "T1", "batch_id": "B1", "type": "done",
            "schema_version": 2, "status": "cancelled",
            "stop_reason": "client_disconnect", "response": "", "metrics": {},
        })
        asyncio.run(exporter.flush_now("R1", query="q"))
        trace = next(i for i in captured["payload"]["batch"]
                     if i["type"] == "trace-create")
        self.assertEqual(trace["body"]["metadata"]["status"], "cancelled")
        self.assertIn("status:cancelled", trace["body"]["tags"])

    # ---- 006 Batch C：R02/R03/R06 新增契约 ----

    def test_session_id_uses_real_conversation_only(self):
        """sessionId 只在存在真实 conversation_id 时填写（006 R03）。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        for ev in sample_events():
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R1", query="q", conversation_id="conv-1"))
        trace = next(i for i in captured["payload"]["batch"]
                     if i["type"] == "trace-create")
        self.assertEqual(trace["body"]["sessionId"], "conv-1")

    def test_sensitive_content_never_exported(self):
        """合成敏感样本：正常/失败/取消路径均不导出原文（006 R06/V13）。"""
        phone = "13812345678"
        sensitive_query = f"我的手机号是 {phone}，帮我查订单"
        sensitive_answer = f"已收到您的问题，请拨打 {phone} 联系客服"
        sensitive_error = "数据库连接串 jdbc:mysql://内网/订单库，手机号 13998887777"
        captured: dict = {}
        exporter = self._exporter(captured)
        events = [
            {"type": "tool_call", "run_id": "RS", "task_id": "TS", "seq": 1,
             "ts_ms": 0.0, "ts_wall": 1000.0, "tool": "query_order",
             "tool_call_id": "c1", "attempt": 1,
             "args": {"order_id": f"备注{phone}"}},
            {"type": "tool_result", "run_id": "RS", "seq": 2, "ts_ms": 5.0,
             "ts_wall": 1001.0, "tool": "query_order", "tool_call_id": "c1",
             "attempt": 1, "data": {}, "error": sensitive_error,
             "duration_ms": 5.0},
            {"type": "done", "run_id": "RS", "task_id": "TS", "seq": 3,
             "ts_ms": 10.0, "ts_wall": 1002.0, "status": "failed",
             "stop_reason": "tool_failed", "response": sensitive_answer,
             "metrics": {}},
        ]
        for ev in events:
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("RS", query=sensitive_query))
        dumped = json.dumps(captured["payload"], ensure_ascii=False)
        for raw in (sensitive_query, sensitive_answer, sensitive_error, phone):
            self.assertNotIn(raw, dumped, "敏感原文不得进入上报载荷")

    def test_agent_span_real_duration_and_tool_parent(self):
        """006 R02：子 Agent span 用真实起止；工具 span 指向该 Agent。"""
        captured: dict = {}
        exporter = self._exporter(captured)
        events = [
            # 工具尝试由 order_agent 调用：parent_span_id 指向 Agent span
            {"type": "tool_call", "run_id": "R4", "task_id": "T4", "seq": 1,
             "ts_ms": 0.0, "ts_wall": 2000.0, "tool": "query_order",
             "tool_call_id": "c9", "attempt": 1, "args": {"order_id": "O1"},
             "parent_span_id": "agent-span-1"},
            {"type": "tool_result", "run_id": "R4", "seq": 2, "ts_ms": 30.0,
             "ts_wall": 2003.0, "tool": "query_order", "tool_call_id": "c9",
             "attempt": 1, "data": {"found": True}, "error": None,
             "duration_ms": 3000.0, "parent_span_id": "agent-span-1"},
            # 子 Agent 调度：真实起止 2s（不再是同一时间戳）
            {"type": "agent_invoke", "run_id": "R4", "task_id": "T4",
             "seq": 3, "ts_ms": 35.0, "ts_wall": 2003.5, "agent": "order_agent",
             "status": "success", "span_id": "agent-span-1", "attempts": 1,
             "start_wall": 2001.0, "end_wall": 2003.0,
             "parent_span_id": "run-root"},
            {"type": "model_call", "run_id": "R4", "seq": 4, "ts_ms": 5.0,
             "ts_wall": 2001.5, "call_kind": "agent_task:order_agent",
             "model": "mock", "latency_ms": 100.0,
             "parent_span_id": "agent-span-1",
             "usage": {"prompt_tokens": 5, "completion_tokens": 1,
                       "total_tokens": 6, "source": "estimated"}},
            {"type": "done", "run_id": "R4", "task_id": "T4", "seq": 5,
             "ts_ms": 40.0, "ts_wall": 2004.0, "status": "completed",
             "stop_reason": "answered", "response": "ok", "metrics": {}},
        ]
        for ev in events:
            exporter.observe(ev)
        asyncio.run(exporter.flush_now("R4", query="q"))
        batch = captured["payload"]["batch"]
        by_name = {i["body"].get("name"): i["body"] for i in batch}
        agent_span = by_name["agent:order_agent"]
        self.assertEqual(agent_span["id"], "agent-span-1",
                         "子 Agent span 以业务 span_id 作为 Langfuse id，"
                         "供子步骤 parentSpanId 解析")
        start = datetime.fromisoformat(agent_span["startTime"])
        end = datetime.fromisoformat(agent_span["endTime"])
        self.assertAlmostEqual((end - start).total_seconds(), 2.0, places=3,
                               msg="子 Agent span 使用真实起止，非零耗时")
        tool_span = by_name["tool:query_order"]
        self.assertEqual(tool_span["parentSpanId"], "agent-span-1",
                         "工具 span 指向真实父步骤（子 Agent）")
        gen = next(i for i in batch if i["type"] == "generation-create")
        self.assertEqual(gen["body"]["parentSpanId"], "agent-span-1",
                         "agent_task 模型调用挂在子 Agent span 下")

    def test_export_failure_leaves_health_record(self):
        """006 R02/V07：导出失败留下受影响 run 与原因，业务结果不受影响。"""
        def handler(request):
            return httpx.Response(500, text="server error")

        client = httpx.AsyncClient(
            base_url="http://langfuse.example.com",
            transport=httpx.MockTransport(handler),
        )
        exporter = LangfuseExporter(make_config(), client=client)
        for ev in sample_events():
            exporter.observe(ev)
        result = asyncio.run(exporter.flush_now("R1", query="q"))
        self.assertIsNone(result)
        self.assertEqual(exporter.export_health["failure"], 1)
        self.assertEqual(exporter.export_health["success"], 0)
        record = exporter.export_health["recent_failures"][0]
        self.assertEqual(record["run_id"], "R1")
        self.assertIn("500", record["reason"])


if __name__ == "__main__":
    unittest.main()
