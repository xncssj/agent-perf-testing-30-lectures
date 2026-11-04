"""HTTP/SSE 集成测试：真实本地回环连接验证事件到达与终态（H08/H09/H10/H11）。

服务通过 uvicorn 运行在 127.0.0.1 随机端口，使用 httpx 真实网络客户端；
不使用可能缓冲响应的进程内传输。可控阻塞工具用于建立到达顺序断言，
不以固定毫秒阈值作为唯一判据。
"""
from __future__ import annotations

import httpx
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uvicorn
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.harness.contracts import (
    AnswerChunk,
    Decision,
    ModelUsage,
    RunInput,
    ToolCall,
)
from src.main import create_app
from src.tools.base import BaseTool

ORDER_ID = "2024010100001"


class BlockTool(BaseTool):
    """可控阻塞工具：阻塞到 release 事件置位或超时。"""

    name = "block_tool"
    description = "测试用阻塞工具"
    args_model = None

    def __init__(self, blocked: threading.Event, release: threading.Event, config=None):
        super().__init__(config or {"timeout": 20, "retry": 0})
        self.blocked = blocked
        self.release = release

    def _run(self, **kwargs):
        self.blocked.set()
        if not self.release.wait(timeout=15):
            raise TimeoutError("测试阻塞工具超时")
        return {"found": True, "order_id": kwargs.get("order_id", "")}


class ScriptedProvider:
    """脚本化 Provider（异步契约），供集成测试注入服务。"""

    evidence_kind = "mock"
    model = "scripted"

    def __init__(self, script=None, answer_chunks=("答", "案"), fail_stream_at=None):
        self.script = list(script or [])
        self.answer_chunks = list(answer_chunks)
        self.fail_stream_at = fail_stream_at  # 第 N 个片段后抛错（H09）
        self.decide_calls = 0

    async def decide(self, messages, tools, context) -> Decision:
        self.decide_calls += 1
        item = self.script.pop(0) if self.script else Decision(action="finalize")
        return item

    async def stream_answer(self, messages, context) -> AsyncIterator[AnswerChunk]:
        for i, ch in enumerate(self.answer_chunks):
            if self.fail_stream_at is not None and i >= self.fail_stream_at:
                from src.llm.base import ProviderError

                raise ProviderError("模拟上游流中断")
            await asyncio_sleep(0.01)
            yield AnswerChunk(content=ch)
        yield AnswerChunk(
            content="", usage=ModelUsage(prompt_tokens=1, completion_tokens=1,
                                         total_tokens=2, source="reported"),
            finish=True,
        )

    async def aclose(self):
        pass


async def asyncio_sleep(s):
    import asyncio

    await asyncio.sleep(s)


def call_tools(*specs) -> Decision:
    return Decision(
        action="call_tools",
        tool_calls=[ToolCall(name=n, arguments=a) for n, a in specs],
        usage=ModelUsage(prompt_tokens=5, completion_tokens=1, total_tokens=6,
                         source="reported"),
    )


class PerQueryProvider:
    """按查询首条消息内容区分运行的脚本化 Provider（并发过载场景使用）。"""

    evidence_kind = "mock"
    model = "scripted"

    def __init__(self):
        self.calls: dict[str, int] = {}
        self.decide_calls = 0

    async def decide(self, messages, tools, context) -> Decision:
        self.decide_calls += 1
        query = next((m.content for m in messages if m.role == "user"), "")
        n = self.calls.get(query, 0)
        self.calls[query] = n + 1
        if n == 0:
            return call_tools(("block_tool", {}))
        return Decision(action="finalize")

    async def stream_answer(self, messages, context) -> AsyncIterator[AnswerChunk]:
        yield AnswerChunk(content="处理完成")
        yield AnswerChunk(content="", usage=ModelUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2,
            source="reported"), finish=True)

    async def aclose(self):
        pass


class _Server:
    """在后台线程运行 uvicorn（127.0.0.1 随机端口，真实网络栈）。"""

    def __init__(self, settings_overrides: dict | None = None):
        self.tmp = tempfile.TemporaryDirectory()
        env_backup = {
            k: os.environ.get(k)
            for k in ("AGENT_DB_PATH", "MOCK_PLAN_LATENCY_MS", "MOCK_TOKEN_LATENCY_MS")
        }
        os.environ["AGENT_DB_PATH"] = str(Path(self.tmp.name) / "it.db")
        os.environ["MOCK_PLAN_LATENCY_MS"] = "10"
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
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0,
                                log_level="error")
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


def parse_sse(resp) -> list[dict]:
    """解析 SSE 响应为事件列表（保留 event 名与 data）。"""
    events = []
    name = ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            events.append({"event": name, **data})
    return events


class TestHttpIntegration(unittest.TestCase):
    """H08/H09/H11：到达顺序、失败终态与并发。"""

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
        self.assertNotEqual(data["run_id"], data["task_id"], "业务 run_id 不与批次/任务混用")
        self.assertIn("上海分拨中心", data["response"])
        self.assertEqual(data["metrics"]["evidence_kind"], "mock")

    def test_version_endpoint_reports_protocol_and_limits(self):
        data = self.client.get("/version").json()
        self.assertIn("protocol_mode", data)
        self.assertIn("effective_limits", data)
        self.assertIn("fingerprint", data)
        self.assertEqual(data["effective_limits"]["tool_workers"],
                         self.server.service.settings.tool_workers)

    def test_metrics_exposes_r3_observability_series(self):
        """R3 观测增补：跑一笔业务后 /metrics 出现准入与 exporter 序列。

        admission 只登记一次每运行（首个发布事件）；exporter_flush 的
        ok/error 序列在模块导入时预建（零值也导出，看板不缺序列）。
        """
        resp = self.client.post("/api/v1/chat/complete", json={
            "user_query": f"我的订单{ORDER_ID}到哪了？",
            "task_id": "T-IT-M", "batch_id": "B-IT", "stream": False,
        })
        self.assertEqual(resp.status_code, 200)
        text = self.client.get("/metrics").text
        self.assertIn("agent_request_admission_seconds_count", text)
        self.assertIn("agent_request_admission_seconds_sum", text)
        self.assertIn('langfuse_exporter_flush_seconds_count{result="ok"}', text)
        self.assertIn('langfuse_exporter_flush_seconds_count{result="error"}', text)
        self.assertIn("harness_tool_calls_inflight", text)

    def test_h08_sse_ordering_with_blocking_tool(self):
        """先收到 tool_call，解除阻塞后才收到 tool_result（真实 HTTP 到达顺序）。"""
        blocked, release = threading.Event(), threading.Event()
        self.server.service.registry.register(
            BlockTool(blocked, release, {"timeout": 20, "retry": 0})
        )
        provider = ScriptedProvider(script=[
            call_tools(("block_tool", {}), ("query_order", {"order_id": ORDER_ID})),
            Decision(action="finalize"),
        ])
        self.server.service.provider = provider
        self.server.service.runner.provider = provider

        events = []
        with self.client.stream("POST", "/api/v1/chat/stream", json={
            "user_query": "q", "task_id": "T-H08", "batch_id": "B-H08",
        }) as resp:
            self.assertEqual(resp.status_code, 200)
            saw_tool_call = False
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    events.append({"event": name, **data})
                    if data.get("type") == "tool_call" and not saw_tool_call:
                        saw_tool_call = True
                        # tool_call 已到达，工具仍被阻塞：此后一小段时间内
                        # 不应出现 tool_result（到达顺序断言）
                        time.sleep(0.5)
                        self.assertFalse(
                            any(e.get("type") == "tool_result" for e in events),
                            "阻塞解除前不得收到 tool_result",
                        )
                        release.set()
                    if data.get("status"):  # done
                        break

        types = [e.get("type") or e.get("event") for e in events]
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        self.assertLess(types.index("tool_call"), types.index("tool_result"))
        done = events[-1]
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["stop_reason"], "answered")

    def test_h09_stream_failure_keeps_partial_then_failed(self):
        """答复流中途失败：先收到部分 answer，终态唯一为 failed，不回放伪流式。"""
        provider = ScriptedProvider(
            script=[call_tools(("query_order", {"order_id": ORDER_ID})),
                    Decision(action="finalize")],
            answer_chunks=("部分", "答"),
            fail_stream_at=1,
        )
        self.server.service.provider = provider
        self.server.service.runner.provider = provider

        events = parse_sse_response(self.client.post(
            "/api/v1/chat/stream",
            json={"user_query": "q", "task_id": "T-H09"},
        ))
        answers = [e for e in events if e.get("type") == "answer"]
        self.assertGreaterEqual(len(answers), 1, "失败前先收到部分 answer")
        done = events[-1]
        self.assertEqual(done["event"], "done")
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["stop_reason"], "provider_error")
        self.assertEqual(done["response"], "部分", "部分答复保留但不判合格")

    def test_h11_concurrent_unique_ids(self):
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


def parse_sse_response(resp: httpx.Response) -> list[dict]:
    """解析非流式调用的 SSE 响应体为事件列表。"""
    events = []
    name = ""
    for line in resp.text.splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            events.append({"event": name, **data})
    return events


class TestDisconnectAndOverload(unittest.TestCase):
    """H10 断连与 H11 过载：需要独立的执行池配置。"""

    def test_h10_client_disconnect_cancels_run(self):
        """客户端硬断连（RST）后：服务端记录取消终态、停止后续调用、
        迟到结果不占用工作槽。

        说明：优雅半关闭（FIN）在 uvicorn/Starlette 组合下不会在响应中途
        触发断连感知（小包写入静默成功），因此使用 RST 硬中断模拟真实
        客户端取消；这也验证了 SSE 心跳写入失败路径的清理。
        """
        import socket as socket_mod
        import struct

        server = _Server()
        try:
            blocked, release = threading.Event(), threading.Event()
            service = server.service
            service.registry.register(
                BlockTool(blocked, release, {"timeout": 20, "retry": 0})
            )
            provider = ScriptedProvider(script=[
                call_tools(("block_tool", {}), ("query_order", {"order_id": ORDER_ID})),
                Decision(action="finalize"),
            ])
            service.provider = provider
            service.runner.provider = provider

            from src.services.agent_service import RUN_TERMINAL_TOTAL

            def cancelled_count() -> float:
                child = RUN_TERMINAL_TOTAL._metrics.get(
                    ("cancelled", "client_disconnect"))
                return child._value.get() if child else 0.0

            host, port = server.base.replace("http://", "").split(":")
            body = json.dumps({"user_query": "q", "task_id": "T-H10"}).encode()
            request = (
                f"POST /api/v1/chat/stream HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode() + body
            sock = socket_mod.create_connection((host, int(port)), timeout=10)
            sock.sendall(request)
            # 读取直到出现 tool_call 事件
            buf = b""
            deadline = time.time() + 20
            while b"tool_call" not in buf and time.time() < deadline:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            self.assertIn(b"tool_call", buf, "应先收到 tool_call 事件")
            # 硬中断：SO_LINGER(1,0) 使 close 发送 RST
            sock.setsockopt(socket_mod.SOL_SOCKET, socket_mod.SO_LINGER,
                            struct.pack("ii", 1, 0))
            sock.close()
            self.assertTrue(blocked.is_set(), "断连时工具应仍被阻塞")

            # 服务端应已记录取消终态（断连触发写入失败/连接层错误 → 取消清理）
            before = cancelled_count()
            deadline = time.time() + 15
            while time.time() < deadline and cancelled_count() <= before:
                time.sleep(0.1)
            self.assertGreater(
                cancelled_count(), before, "断连应在服务端记录取消终态"
            )
            # 解除阻塞：迟到结果不占用工作槽，也不再发起后续模型调用
            release.set()
            deadline = time.time() + 10
            while time.time() < deadline and service.executor.in_flight > 0:
                time.sleep(0.05)
            self.assertEqual(service.executor.in_flight, 0, "迟到结果不占用工作槽")
            time.sleep(0.2)
            self.assertEqual(provider.decide_calls, 1, "断连后不再发起后续模型调用")
        finally:
            server.stop()

    def test_h11_pool_overload_fails_explicitly(self):
        server = _Server({"tool_workers": 1, "tool_queue_size": 0})
        try:
            client = httpx.Client(base_url=server.base, timeout=60)
            blocked, release = threading.Event(), threading.Event()
            service = server.service
            service.registry.register(
                BlockTool(blocked, release, {"timeout": 20, "retry": 0})
            )
            provider = PerQueryProvider()
            service.provider = provider
            service.runner.provider = provider

            results = {}

            def run_one(tag):
                resp = client.post("/api/v1/chat/complete", json={
                    "user_query": tag, "task_id": f"T-OV-{tag}"})
                results[tag] = resp.json()

            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(run_one, "A")
                self.assertTrue(blocked.wait(timeout=10), "A 的工具应占住唯一工作槽")
                f2 = pool.submit(run_one, "B")
                # B 必须在 A 仍阻塞期间完成：过载快速失败，不无限排队
                deadline = time.time() + 10
                while time.time() < deadline and "B" not in results:
                    time.sleep(0.05)
                self.assertIn("B", results, "过载应在 A 阻塞期间快速失败")
                release.set()
                f1.result(timeout=30)
            statuses = {tag: r["status"] for tag, r in results.items()}
            self.assertEqual(statuses.get("A"), "completed")
            self.assertEqual(results["B"]["status"], "failed", "过载必须有明确失败")
            self.assertEqual(results["B"]["stop_reason"], "tool_failed")
            overload_detail = json.dumps(results["B"].get("trace", {}), ensure_ascii=False)
            self.assertIn("过载", overload_detail, "trace 应记录过载明细")
            client.close()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
