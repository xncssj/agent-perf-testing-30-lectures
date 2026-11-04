"""Java 微服务 HTTP 工具的单元测试（java_service_tool）。

覆盖口径：
- 响应映射：Java 驼峰字段 → 直读版 snake_case 键集；JSON 文本列解析为对象；
  @JsonInclude(NON_NULL) 缺失字段补齐 None——答复生成与业务判定按直读版
  dict 结构消费，切换链路对其必须无感知。
- notFound 语义：Java 侧未命中返回 HTTP 200 + success=false，按 body 判定。
- 超时转译：httpx 超时 → TimeoutError → BaseTool.execute 记 timed_out=True，
  共享执行器只对 timed_out 重试，此形态是接入重试链路的依据。
- settings：JAVA_SERVICE_ENABLED / JAVA_SERVICE_URL 读取与 fail fast。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# 共享包 app/harness（src.tools.params 经 shim 引用 harness.params）：
# 需 case 根在 sys.path，导入顺序上先于任何 shim 到位
CASE_ROOT = PROJECT_ROOT.parents[1]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from src.tools.java_service_tool import (  # noqa: E402
    JavaLogisticsTool,
    JavaOrderTool,
    JavaServiceClient,
)


def api_response(payload: dict) -> httpx.Response:
    """模拟 java-order-logistics 的统一 ApiResponse 包装。"""
    return httpx.Response(200, json=payload)


ORDER_DATA = {
    "orderId": "2024010100001",
    "userId": "U00001",
    "status": "shipped",
    "items": '[{"product_id":"P001","quantity":1}]',  # Java 实体是 String 存 JSON
    "totalAmount": 8999,
    "shippingAddress": '{"province":"上海市"}',
    "trackingNo": "SF00000001",
    "carrier": "顺丰速运",
    "createTime": "20240101 10:01:00",
    "updateTime": "20240101 15:01:00",
}

LOGISTICS_DATA = {
    "trackingNo": "SF00000001",
    "orderId": "2024010100001",
    "carrier": "顺丰速运",
    "status": "运输中",
    "currentLocation": "上海分拨中心",
    "origin": "上海市仓",
    "destination": "收货地分拨中心",
    "trajectory": '[{"time":"20240101 08:00:00","status":"揽收"}]',
    "estimatedDelivery": "20240102 18:00:00",
}


def make_client(handler) -> JavaServiceClient:
    return JavaServiceClient("http://java-service:8080", transport=httpx.MockTransport(handler))


class TestJavaOrderTool(unittest.TestCase):
    def test_found_maps_to_direct_read_shape(self):
        client = make_client(lambda req: api_response({"success": True, "data": ORDER_DATA}))
        tool = JavaOrderTool(client)
        outcome = tool.execute(order_id="2024010100001")
        self.assertIsNone(outcome.error)
        self.assertTrue(outcome.result["found"])
        order = outcome.result["order"]
        # snake_case 键集与直读版一致（DB 版 dict 的完整键）
        self.assertEqual(
            {"order_id", "user_id", "status", "items", "total_amount",
             "shipping_address", "tracking_no", "carrier", "create_time", "update_time"},
            set(order),
        )
        self.assertEqual("2024010100001", order["order_id"])
        self.assertEqual(8999, order["total_amount"])
        # JSON 文本列已解析为对象，与直读版 _row_to_dict 行为一致
        self.assertEqual(1, len(order["items"]))
        self.assertEqual("上海市", order["shipping_address"]["province"])

    def test_not_found_returns_found_false(self):
        client = make_client(lambda req: api_response(
            {"success": False, "message": "订单不存在: X", "code": "NOT_FOUND"}
        ))
        tool = JavaOrderTool(client)
        outcome = tool.execute(order_id="X")
        self.assertIsNone(outcome.error)
        self.assertEqual({"found": False, "order_id": "X", "message": "订单不存在: X"}, outcome.result)

    def test_missing_order_id_raises_value_error(self):
        client = make_client(lambda req: api_response({"success": True, "data": {}}))
        tool = JavaOrderTool(client)
        outcome = tool.execute()
        self.assertIn("缺少 order_id", outcome.error or "")

    def test_timeout_records_timed_out(self):
        def handler(req):
            raise httpx.ConnectTimeout("timed out")

        tool = JavaOrderTool(make_client(handler))
        outcome = tool.execute(order_id="2024010100001")
        self.assertTrue(outcome.timed_out, "httpx 超时必须转译为 timed_out=True 才能接入执行器重试")
        self.assertEqual({}, outcome.result)

    def test_http_error_records_error(self):
        client = make_client(lambda req: httpx.Response(500, text="boom"))
        tool = JavaOrderTool(client)
        outcome = tool.execute(order_id="2024010100001")
        self.assertFalse(outcome.timed_out)
        self.assertIn("HTTP 500", outcome.error or "")


class TestJavaLogisticsTool(unittest.TestCase):
    def test_by_tracking_no(self):
        seen_paths = []

        def handler(req):
            seen_paths.append(req.url.path)
            return api_response({"success": True, "data": LOGISTICS_DATA})

        tool = JavaLogisticsTool(make_client(handler))
        outcome = tool.execute(tracking_no="SF00000001")
        self.assertEqual(["/api/logistics/SF00000001"], seen_paths)
        self.assertTrue(outcome.result["found"])
        logistics = outcome.result["logistics"]
        self.assertEqual(
            {"tracking_no", "order_id", "carrier", "status", "current_location",
             "origin", "destination", "trajectory", "estimated_delivery"},
            set(logistics),
        )
        self.assertEqual("上海分拨中心", logistics["current_location"])
        self.assertEqual(1, len(logistics["trajectory"]))

    def test_by_order_id_uses_order_endpoint(self):
        seen_paths = []

        def handler(req):
            seen_paths.append(req.url.path)
            return api_response({"success": True, "data": LOGISTICS_DATA})

        tool = JavaLogisticsTool(make_client(handler))
        outcome = tool.execute(order_id="2024010100001")
        self.assertEqual(["/api/logistics/order/2024010100001"], seen_paths)
        self.assertTrue(outcome.result["found"])

    def test_missing_args_raises_value_error(self):
        tool = JavaLogisticsTool(make_client(lambda req: api_response({"success": True})))
        outcome = tool.execute()
        self.assertIn("缺少 tracking_no", outcome.error or "")


class TestJavaServiceSettings(unittest.TestCase):
    """JAVA_SERVICE_ENABLED / JAVA_SERVICE_URL 的读取与 fail fast。"""

    def _make_settings(self, env: dict):
        from src.config.settings import Settings

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "agent_config.yaml"
            cfg.write_text("agent:\n  name: t\n", encoding="utf-8")
            saved = {k: os.environ.get(k) for k in env}
            try:
                for key, value in env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                return Settings(config_path=cfg)
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_default_disabled(self):
        settings = self._make_settings({"JAVA_SERVICE_ENABLED": None, "JAVA_SERVICE_URL": None})
        self.assertFalse(settings.java_service["enabled"])

    def test_enabled_with_url(self):
        settings = self._make_settings(
            {"JAVA_SERVICE_ENABLED": "true", "JAVA_SERVICE_URL": "http://java:8080/"}
        )
        self.assertTrue(settings.java_service["enabled"])
        # 尾斜杠去除，拼路径不出现双斜杠
        self.assertEqual("http://java:8080", settings.java_service["base_url"])

    def test_enabled_without_url_fails_fast(self):
        with self.assertRaises(ValueError):
            self._make_settings({"JAVA_SERVICE_ENABLED": "true", "JAVA_SERVICE_URL": None})

    def test_fingerprint_distinguishes_link(self):
        disabled = self._make_settings({"JAVA_SERVICE_ENABLED": None, "JAVA_SERVICE_URL": None})
        enabled = self._make_settings(
            {"JAVA_SERVICE_ENABLED": "true", "JAVA_SERVICE_URL": "http://java:8080"}
        )
        self.assertFalse(disabled.to_fingerprint_fields()["java_service_enabled"])
        self.assertTrue(enabled.to_fingerprint_fields()["java_service_enabled"])


if __name__ == "__main__":
    unittest.main()
