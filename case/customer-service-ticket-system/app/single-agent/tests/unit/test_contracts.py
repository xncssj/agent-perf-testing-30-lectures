"""契约层单元测试：工具参数模型、消息与调用关联、结果结构（H03/H04 部分）。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError

from src.tools.params import (
    QueryLogisticsArgs,
    QueryOrderArgs,
    SearchKnowledgeArgs,
    TOOL_SCHEMAS,
)


class TestToolParamModels(unittest.TestCase):
    """工具参数模型：禁止未声明字段，必填约束明确。"""

    def test_order_args_ok(self):
        args = QueryOrderArgs(order_id="2024010100001")
        self.assertEqual(args.order_id, "2024010100001")
        self.assertEqual(args.model_dump(), {"order_id": "2024010100001"})

    def test_order_args_empty_rejected(self):
        with self.assertRaises(ValidationError):
            QueryOrderArgs(order_id="")

    def test_order_args_extra_field_rejected(self):
        with self.assertRaises(ValidationError):
            QueryOrderArgs(order_id="2024010100001", tracking_no="SF123456")

    def test_logistics_args_tracking_no_only(self):
        args = QueryLogisticsArgs(tracking_no="SF1234567890")
        self.assertEqual(args.tracking_no, "SF1234567890")

    def test_logistics_args_order_id_only(self):
        args = QueryLogisticsArgs(order_id="2024010100001")
        self.assertEqual(args.order_id, "2024010100001")

    def test_logistics_args_both_allowed(self):
        """同时提供时允许（tracking_no 优先由工具实现保证）。"""
        args = QueryLogisticsArgs(tracking_no="SF1234567890", order_id="2024010100001")
        self.assertTrue(args.tracking_no and args.order_id)

    def test_logistics_args_none_rejected(self):
        with self.assertRaises(ValidationError):
            QueryLogisticsArgs()

    def test_knowledge_args_empty_rejected(self):
        with self.assertRaises(ValidationError):
            SearchKnowledgeArgs(query="")

    def test_knowledge_args_ok(self):
        self.assertEqual(SearchKnowledgeArgs(query="退货").query, "退货")

    def test_tool_schemas_cover_business_tools(self):
        self.assertEqual(
            set(TOOL_SCHEMAS), {"query_order", "query_logistics", "search_knowledge"}
        )
        for schema in TOOL_SCHEMAS.values():
            self.assertEqual(schema["parameters"]["type"], "object")


class TestContracts(unittest.TestCase):
    """消息、调用与结果契约。"""

    def test_message_tool_call_pairing(self):
        from src.harness.contracts import Message, ToolCall

        call = ToolCall(name="query_order", arguments={"order_id": "O1"})
        self.assertTrue(call.id, "工具调用必须自带 ID")
        assistant = Message(role="assistant", content="", tool_calls=[call])
        observation = Message(
            role="tool", tool_call_id=call.id, name="query_order", content="{}"
        )
        self.assertEqual(assistant.tool_calls[0].id, observation.tool_call_id)

    def test_run_result_defaults(self):
        from src.harness.contracts import RunResult

        result = RunResult(
            task_id="T1", run_id="R1", status="completed",
            stop_reason="answered",
        )
        self.assertEqual(result.schema_version, 2)
        # 指标可 JSON 序列化（含 None 空值）
        json.dumps(result.model_dump())

    def test_run_result_failed_keeps_partial(self):
        from src.harness.contracts import RunResult

        result = RunResult(
            task_id="T1", run_id="R1", status="failed",
            stop_reason="provider_error", response="部分答复",
            error="连接中断",
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.response, "部分答复")


if __name__ == "__main__":
    unittest.main()
