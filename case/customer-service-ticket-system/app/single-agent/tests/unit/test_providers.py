"""Provider 单元测试：Mock 决策规则与 OpenAI 原生/JSON 双协议（H03）。

真实 Provider 使用 httpx MockTransport 验证请求体、分片、用量与错误；
这不证明外部模型已调用成功。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from src.harness.contracts import Message, RunContext, ToolCall
from src.llm.base import InvalidDecisionError, ProviderError
from src.llm.mock_provider import MockProvider
from src.llm.openai_provider import OpenAIProvider

TOOLS = {
    "query_order": {
        "description": "查询订单信息",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                       "required": ["order_id"]},
    },
    "query_logistics": {
        "description": "查询物流状态",
        "parameters": {"type": "object", "properties": {"tracking_no": {"type": "string"}}},
    },
    "search_knowledge": {
        "description": "搜索知识库",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]},
    },
}


def make_ctx(query="") -> RunContext:
    import time

    return RunContext(task_id="T", run_id="R", query=query,
                      deadline=time.monotonic() + 30)


def make_settings(**kw):
    base = dict(
        model_name="mock", mock_seed=42, mock_plan_latency_ms=1,
        mock_plan_jitter_ms=1, mock_token_latency_ms=1, temperature=0,
        max_tokens=100, llm_base_url="http://llm.example.com/v1",
        llm_api_key="sk-test", llm_stream_include_usage=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestMockProvider(unittest.TestCase):
    def setUp(self):
        self.provider = MockProvider(make_settings())

    def _decide(self, query, history=None):
        messages = history or [
            Message(role="system", content="sys"),
            Message(role="user", content=query),
        ]
        return asyncio.run(self.provider.decide(messages, TOOLS, make_ctx(query)))

    def test_logistics_query_plans_order_then_logistics(self):
        q = "我的订单2024010100001到哪了？"
        d1 = self._decide(q)
        self.assertEqual(d1.action, "call_tools")
        self.assertEqual(d1.tool_calls[0].name, "query_order")
        self.assertEqual(d1.tool_calls[0].arguments.get("order_id"), "2024010100001")
        self.assertEqual(d1.usage.source, "estimated", "Mock 用量为模拟数据")

        history = [
            Message(role="system", content="sys"),
            Message(role="user", content=q),
            Message(role="assistant", content="", tool_calls=d1.tool_calls),
            Message(role="tool", tool_call_id=d1.tool_calls[0].id, content="{}"),
        ]
        d2 = self._decide(q, history)
        self.assertEqual(d2.action, "call_tools")
        self.assertEqual(d2.tool_calls[0].name, "query_logistics")

    def test_tracking_no_only_skips_order_tool(self):
        d = self._decide("运单SF1234567890到哪了？")
        self.assertEqual(d.tool_calls[0].name, "query_logistics")
        self.assertEqual(d.tool_calls[0].arguments.get("tracking_no"), "SF1234567890")

    def test_missing_args_falls_back_to_clarify(self):
        d = self._decide("那个东西到哪了")
        self.assertEqual(d.action, "clarify")

    def test_complaint_maps_to_handoff(self):
        d = self._decide("我要投诉！")
        self.assertEqual(d.action, "handoff")

    def test_return_policy_uses_knowledge(self):
        d = self._decide("退货政策是什么")
        self.assertEqual(d.tool_calls[0].name, "search_knowledge")

    def test_stream_answer_reads_aggregate_tool_message(self):
        agg = json.dumps({
            "order": None,
            "logistics": {"found": True, "logistics": {
                "order_id": "2024010100001", "current_location": "上海分拨中心",
                "estimated_delivery": "明天 20:00 前",
            }},
            "knowledge": None,
        }, ensure_ascii=False)
        messages = [
            Message(role="system", content="sys"),
            Message(role="tool", content=agg),
            Message(role="user", content="生成答复"),
        ]
        chunks = []

        async def collect():
            async for c in self.provider.stream_answer(messages, make_ctx()):
                chunks.append(c)

        asyncio.run(collect())
        text = "".join(c.content for c in chunks)
        self.assertIn("2024010100001", text)
        self.assertIn("上海分拨中心", text)
        self.assertTrue(chunks[-1].finish)
        self.assertIsNotNone(chunks[-1].usage)
        self.assertEqual(chunks[-1].usage.source, "estimated")


class _OpenAIHarness:
    """用 httpx MockTransport 驱动 OpenAIProvider。"""

    def __init__(self, handler, mode="native", **settings_kw):
        from src.agent.prompt_manager import PromptManager

        prompts = PROJECT_ROOT / "src" / "prompts"
        self.provider = OpenAIProvider(
            make_settings(**settings_kw), PromptManager(prompts), mode=mode
        )
        self.provider._client = httpx.AsyncClient(
            base_url=self.provider.base_url,
            transport=httpx.MockTransport(handler),
            auth=("pk-lf-test", "sk-lf-test"),
        )

    async def aclose(self):
        await self.provider.aclose()


class TestOpenAINativeProvider(unittest.TestCase):
    def test_tool_calls_parsed_into_decision(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["payload"] = json.loads(request.content)
            return httpx.Response(200, json={
                "choices": [{"message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {"name": "query_order",
                                     "arguments": "{\"order_id\": \"2024010100001\"}"},
                    }],
                }}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            })

        h = _OpenAIHarness(handler)
        d = asyncio.run(h.provider.decide(
            [Message(role="system", content="s"), Message(role="user", content="q")],
            TOOLS, make_ctx(),
        ))
        self.assertEqual(d.action, "call_tools")
        self.assertEqual(d.tool_calls[0].id, "call_1")
        self.assertEqual(d.tool_calls[0].arguments["order_id"], "2024010100001")
        self.assertEqual(d.usage.source, "reported")
        # 原生协议提交了工具定义与控制函数
        names = [t["function"]["name"] for t in captured["payload"]["tools"]]
        self.assertIn("query_order", names)
        self.assertIn("finish", names)

    def test_control_tool_maps_to_finalize_with_pairing(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": None,
                                         "tool_calls": [{
                                             "id": "c2", "type": "function",
                                             "function": {"name": "finish",
                                                          "arguments": "{}"},
                                         }]}}],
                "usage": {},
            })

        h = _OpenAIHarness(handler)
        d = asyncio.run(h.provider.decide(
            [Message(role="user", content="q")], TOOLS, make_ctx(),
        ))
        self.assertEqual(d.action, "finalize")
        self.assertEqual(len(d.control_calls), 1, "控制调用需保留供历史配对")
        self.assertEqual(d.control_calls[0].name, "finish")

    def test_mixed_control_and_tools_is_invalid(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "query_order", "arguments": "{}"}},
                    {"id": "c2", "type": "function",
                     "function": {"name": "finish", "arguments": "{}"}},
                ]}}],
                "usage": {},
            })

        h = _OpenAIHarness(handler)
        with self.assertRaises(InvalidDecisionError):
            asyncio.run(h.provider.decide(
                [Message(role="user", content="q")], TOOLS, make_ctx(),
            ))

    def test_conflicting_controls_invalid(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "finish", "arguments": "{}"}},
                    {"id": "c2", "type": "function",
                     "function": {"name": "complaint", "arguments": "{}"}},
                ]}}],
                "usage": {},
            })

        h = _OpenAIHarness(handler)
        with self.assertRaises(InvalidDecisionError):
            asyncio.run(h.provider.decide(
                [Message(role="user", content="q")], TOOLS, make_ctx(),
            ))

    def test_unknown_tool_call_invalid(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"tool_calls": [
                    {"id": "c9", "type": "function",
                     "function": {"name": "hack_tool", "arguments": "{}"}},
                ]}}],
                "usage": {},
            })

        h = _OpenAIHarness(handler)
        with self.assertRaises(InvalidDecisionError):
            asyncio.run(h.provider.decide(
                [Message(role="user", content="q")], TOOLS, make_ctx(),
            ))

    def test_unsupported_endpoint_fails_without_retry(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(400, json={"error": {"message": "tools unsupported"}})

        h = _OpenAIHarness(handler)
        with self.assertRaises(ProviderError):
            asyncio.run(h.provider.decide(
                [Message(role="user", content="q")], TOOLS, make_ctx(),
            ))
        self.assertEqual(calls["n"], 1, "不支持原生工具时明确失败，不自动二次调用")

    def test_stream_answer_yields_chunks_and_usage(self):
        sse = "\n\n".join([
            'data: ' + json.dumps({"choices": [{"delta": {"content": "您"}}]}),
            'data: ' + json.dumps({"choices": [{"delta": {"content": "好"}}]}),
            'data: ' + json.dumps({
                "choices": [],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
            }),
            "data: [DONE]",
        ])

        def handler(request):
            return httpx.Response(
                200, content=sse.encode(), headers={"content-type": "text/event-stream"}
            )

        h = _OpenAIHarness(handler)
        chunks = []

        async def collect():
            async for c in h.provider.stream_answer([], make_ctx()):
                chunks.append(c)

        asyncio.run(collect())
        text = "".join(c.content for c in chunks)
        self.assertEqual(text, "您好")
        self.assertEqual(chunks[-1].usage.total_tokens, 9)


class TestOpenAIJSONProvider(unittest.TestCase):
    def test_valid_json_decision(self):
        def handler(request):
            payload = json.loads(request.content)
            # 请求末条 user 消息应包含决策提示词与观察结果
            self.assertIn("选择合适的工具", payload["messages"][-1]["content"])
            return httpx.Response(200, json={
                "choices": [{"message": {"content":
                             json.dumps({"tool": "query_order",
                                         "args": {"order_id": "2024010100001"}})}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            })

        h = _OpenAIHarness(handler, mode="json")
        d = asyncio.run(h.provider.decide(
            [Message(role="system", content="s"), Message(role="user", content="q")],
            TOOLS, make_ctx(),
        ))
        self.assertEqual(d.action, "call_tools")
        self.assertEqual(d.tool_calls[0].arguments["order_id"], "2024010100001")

    def test_control_json(self):
        for tool, action in (("finish", "finalize"),
                             ("ask_clarification", "clarify"),
                             ("complaint", "handoff")):
            def handler(request, _tool=tool):
                return httpx.Response(200, json={
                    "choices": [{"message": {"content":
                                 json.dumps({"tool": _tool, "args": {}})}}],
                    "usage": {},
                })

            h = _OpenAIHarness(handler, mode="json")
            d = asyncio.run(h.provider.decide(
                [Message(role="user", content="q")], TOOLS, make_ctx(),
            ))
            self.assertEqual(d.action, action, tool)

    def test_illegal_json_is_invalid_decision(self):
        """非法 JSON 不按“无工具可调用”结束。"""
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "我想想……"}}], "usage": {},
            })

        h = _OpenAIHarness(handler, mode="json")
        with self.assertRaises(InvalidDecisionError):
            asyncio.run(h.provider.decide(
                [Message(role="user", content="q")], TOOLS, make_ctx(),
            ))

    def test_json_mode_never_sends_tool_role(self):
        """JSON 兼容协议不得发送缺失调用 ID 的 tool 消息。"""
        def handler(request):
            payload = json.loads(request.content)
            for m in payload["messages"]:
                self.assertNotEqual(m["role"], "tool", "JSON 模式不应发送 tool 角色")
            return httpx.Response(200, json={
                "choices": [{"message": {"content":
                             json.dumps({"tool": "finish", "args": {}})}}],
                "usage": {},
            })

        h = _OpenAIHarness(handler, mode="json")
        history = [
            Message(role="system", content="s"),
            Message(role="user", content="q"),
            Message(role="assistant", content="", tool_calls=[
                ToolCall(name="query_order", arguments={"order_id": "O1"})]),
            Message(role="tool", tool_call_id="tc1", name="query_order",
                    content='{"found": false}'),
        ]
        asyncio.run(h.provider.decide(history, TOOLS, make_ctx()))


if __name__ == "__main__":
    unittest.main()
