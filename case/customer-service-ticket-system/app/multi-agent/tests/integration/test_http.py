"""HTTP/SSE 集成测试（阶段二 v2）：真实回环连接验证终态、事件序列与并发。

服务通过 uvicorn 运行在 127.0.0.1 随机端口，使用 httpx 真实网络客户端。
断连取消路径与阶段一共用同一服务层实现（阶段一集成测试已覆盖），
本文件聚焦 Multi-Agent 特有契约：编排事件、Worker 轨迹与并发身份。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import uvicorn
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import AsyncIterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

import httpx  # noqa: E402

from src.main import create_app  # noqa: E402
from harness.contracts import AnswerChunk, Decision, ModelUsage, RunContext  # noqa: E402
from harness.contracts import ToolCall  # noqa: E402

ORDER_ID = "2024010100001"


class ScriptedProvider:
    """脚本化 Provider（异步契约），供集成测试注入服务。"""

    evidence_kind = "mock"
    model = "scripted"

    def __init__(self, answer_chunks=("答", "案")):
        self.answer_chunks = list(answer_chunks)
        self.decide_calls = 0

    async def plan(self, query, context: RunContext):
        self.decide_calls += 1
        from src.mas.provider import PlanResult

        return PlanResult(
            intent="logistics_query",
            agents=["order_agent", "logistics_agent"],
            model=self.model, latency_ms=1.0,
            usage=ModelUsage(prompt_tokens=5, completion_tokens=2,
                             total_tokens=7, source="reported"),
        )

    async def agent_task(self, agent, query, context: RunContext):
        from src.mas.provider import AgentTaskResult

        tool = {"order_agent": "query_order",
                "logistics_agent": "query_logistics"}.get(agent)
        return AgentTaskResult(
            tool=tool, args={"order_id": ORDER_ID}, model=self.model,
            latency_ms=1.0,
            usage=ModelUsage(prompt_tokens=4, completion_tokens=1,
                             total_tokens=5, source="reported"),
        )

    async def validation(self, query, facts, context: RunContext):
        from src.mas.provider import ValidationVerdict

        return ValidationVerdict(status="complete", model=self.model,
                                 latency_ms=1.0,
                                 usage=ModelUsage(source="estimated"))

    async def stream_answer(self, messages, context: RunContext) -> AsyncIterator[AnswerChunk]:
        for ch in self.answer_chunks:
            yield AnswerChunk(content=ch)
        yield AnswerChunk(content="", usage=ModelUsage(prompt_tokens=9,
                                                       completion_tokens=2,
                                                       total_tokens=11,
                                                       source="reported"),
                          finish=True)

    async def aclose(self):
        pass


class _Server:
    """在后台线程运行 uvicorn（127.0.0.1 随机端口，真实网络栈）。"""

    def __init__(self, settings_overrides: dict | None = None):
        import os
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        env_backup = {
            k: os.environ.get(k)
            for k in ("AGENT_DB_PATH", "MOCK_PLAN_LATENCY_MS",
                      "MOCK_AGENT_LATENCY_MS", "MOCK_VALIDATION_LATENCY_MS",
                      "MOCK_TOKEN_LATENCY_MS")
        }
        os.environ["AGENT_DB_PATH"] = str(Path(self.tmp.name) / "it.db")
        for k in ("MOCK_PLAN_LATENCY_MS", "MOCK_AGENT_LATENCY_MS",
                  "MOCK_VALIDATION_LATENCY_MS"):
            os.environ[k] = "10"
        os.environ["MOCK_TOKEN_LATENCY_MS"] = "1"
        try:
            from src.config.settings import Settings

            settings = Settings()
        finally:
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        for k, v in (settings_overrides or {}).items():
            setattr(settings, k, v)
        self.app = create_app(settings)
        self.service = self.app.state.service
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self.server.started:
            raise RuntimeError("集成测试服务启动失败")
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.tmp.cleanup()


class TestHttpIntegration(unittest.TestCase):
    def setUp(self):
        self.server = _Server()
        self.client = httpx.Client(base_url=self.server.base, timeout=60)

    def tearDown(self):
        self.client.close()
        self.server.stop()

    def test_complete_endpoint_v2_contract(self):
        resp = self.client.post("/api/v1/chat/complete", json={
            "user_query": f"我的订单{ORDER_ID}到哪了？",
            "task_id": "T-IT-1",
            "batch_id": "B-IT",
            "stream": False,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["stop_reason"], "answered")
        self.assertEqual(data["task_id"], "T-IT-1")
        self.assertEqual(data["batch_id"], "B-IT")
        self.assertTrue(data["run_id"])
        self.assertIn("上海分拨中心", data["response"])
        # Multi-Agent 轨迹：规划清单、Worker 身份与 span、校验结论
        trace = data["metrics"]["trace"]
        self.assertIn("order_agent", trace["plan_agents"])
        self.assertIn("validation_agent", trace["plan_agents"])
        self.assertGreater(trace["fanout"]["efficiency"], 1.0)
        for a in trace["agents"]:
            self.assertEqual(a["parent_span_id"], data["run_id"])
            self.assertTrue(a["span_id"])
        self.assertEqual(trace["validation"]["status"], "complete")

    def test_version_endpoint_reports_orchestration(self):
        data = self.client.get("/version").json()
        self.assertEqual(data["protocol_mode"]["orchestration"], "multi-agent")
        self.assertIn("parallel_limit", data["effective_limits"])
        self.assertIn("fingerprint", data)

    def test_sse_full_flow_events(self):
        events = []
        name = ""
        with self.client.stream("POST", "/api/v1/chat/stream", json={
            "user_query": f"订单{ORDER_ID}到哪了",
            "task_id": "T-SSE",
            "batch_id": "B-SSE",
        }) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    events.append({"event": name, **data})
                    # 仅 done 事件终止（agent_invoke 等编排事件也携带 status 字段）
                    if name == "done":
                        break
        types = [e.get("type") or e.get("event") for e in events]
        self.assertEqual(types[0], "thinking")
        self.assertEqual(types[-1], "done")
        self.assertIn("agent_invoke", types)
        self.assertIn("validation", types)
        self.assertIn("answer", types)
        done = events[-1]
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["stop_reason"], "answered")
        # seq 严格递增且不重复
        seqs = [e["seq"] for e in events if "seq" in e]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_concurrent_unique_ids(self):
        def one(i):
            resp = self.client.post("/api/v1/chat/complete", json={
                "user_query": f"订单{ORDER_ID}到哪了",
                "task_id": f"T-C{i}",
                "batch_id": "B-CONC",
            })
            return resp.json()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(one, range(8)))
        run_ids = {r["run_id"] for r in results}
        self.assertEqual(len(run_ids), 8, "并发 run_id 不重复")
        for r in results:
            self.assertEqual(r["status"], "completed")
            self.assertIn("上海分拨中心", r["response"])
        # Worker span 不串扰：全部 parent = 各自根 run_id
        for r in results:
            for a in r["metrics"]["trace"]["agents"]:
                self.assertEqual(a["parent_span_id"], r["run_id"])


if __name__ == "__main__":
    unittest.main()
