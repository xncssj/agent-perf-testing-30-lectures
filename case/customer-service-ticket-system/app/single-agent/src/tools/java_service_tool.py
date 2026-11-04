"""Java 订单物流微服务的 HTTP 工具（Java 服务设计说明 §6 链路开关的落地实现）。

启用 `java_service.enabled`（JAVA_SERVICE_ENABLED=true）时，query_order /
query_logistics 不再进程内直读快照库，而是经 HTTP 调用 java-order-logistics
微服务（GET /api/orders/{orderId}、GET /api/logistics/{trackingNo}、
GET /api/logistics/order/{orderId}），复现「Agent 依赖下游微服务」的真实链路：
多一跳 HTTP 与 Java 侧 HikariCP 连接池，性能基线口径与直读模式不可混用。

返回结构与直读版工具完全一致（{"found": bool, "order"/"logistics": dict}，
键名 snake_case、JSON 文本列已解析为对象、键集与直读版对齐），答复生成与
业务判定对切换无感知。

超时与重试口径：请求级超时复用 tools.query_order/query_logistics 的 timeout
配置（秒）；httpx 超时转译为 TimeoutError 抛出，使 BaseTool.execute 记录
timed_out=True——共享执行器（harness.tool_executor）只对 timed_out 重试，
这是接入既有重试链路的必要形态。工具内部不做重试，重试次数由执行器按
工具 retry 配置承担。
"""
from __future__ import annotations

import json
from urllib.parse import quote

import httpx

from ..harness.trace_context import dependency_headers

from ..tools.base import BaseTool
from ..tools.params import QueryLogisticsArgs, QueryOrderArgs

# Java 侧 Jackson 默认输出驼峰字段名；映射表给出驼峰 → 直读版 snake_case 的
# 全量键集（@JsonInclude(NON_NULL) 缺失字段补 None，下游按键访问不缺键）。
_ORDER_FIELD_MAP = {
    "orderId": "order_id",
    "userId": "user_id",
    "status": "status",
    "items": "items",
    "totalAmount": "total_amount",
    "shippingAddress": "shipping_address",
    "trackingNo": "tracking_no",
    "carrier": "carrier",
    "createTime": "create_time",
    "updateTime": "update_time",
}
_LOGISTICS_FIELD_MAP = {
    "trackingNo": "tracking_no",
    "orderId": "order_id",
    "carrier": "carrier",
    "status": "status",
    "currentLocation": "current_location",
    "origin": "origin",
    "destination": "destination",
    "trajectory": "trajectory",
    "estimatedDelivery": "estimated_delivery",
}
# Java 实体的 JSON 文本列（items/shipping_address/trajectory 是 String 存 JSON），
# 经 _as_json 解析为对象，与直读版 _row_to_dict 的行为一致。
_JSON_COLUMNS = ("items", "shipping_address", "trajectory")


class JavaServiceClient:
    """java-order-logistics 微服务的 HTTP 客户端（线程安全，可共享）。

    httpx.Client 线程安全；工具在执行池（tool_workers 上限）中并发调用，
    默认连接池容量足够，不再单独配置 limits。生命周期由 AgentService
    持有：组装时创建、aclose 时关闭。
    """

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        # base_url 已在 Settings 中去除尾斜杠；此处原样拼路径。
        # transport 仅供单元测试注入 MockTransport，生产保持默认连接池。
        self._client = httpx.Client(base_url=base_url, transport=transport)

    def get_json(self, path: str, timeout_s: float) -> dict:
        """GET 并返回 JSON 体；超时转译 TimeoutError，其余异常统一 RuntimeError。

        每次请求自动携带当前运行的依赖上下文头（traceparent /
        x-agent-run-id / x-agent-tool-call-id，006 R03）；上下文缺失时
        不伪造身份头，由 Java 侧按未关联请求处理。
        """
        try:
            resp = self._client.get(
                path, timeout=timeout_s, headers=dependency_headers(),
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"java_service 请求超时: {path}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"java_service 请求失败: {path} ({exc})") from exc
        if resp.status_code // 100 != 2:
            raise RuntimeError(f"java_service HTTP {resp.status_code}: {path}")
        try:
            return resp.json()
        except ValueError as exc:
            raise RuntimeError(f"java_service 响应非 JSON: {path}") from exc

    def close(self) -> None:
        self._client.close()


def _as_json(value):
    """JSON 文本列 → 对象；解析失败或非字符串原样保留（对齐直读版）。"""
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalize(data: dict, field_map: dict[str, str]) -> dict:
    """Java 驼峰字段 → 直读版 snake_case 键集，JSON 文本列同步解析。"""
    row: dict = {}
    for java_key, key in field_map.items():
        if key in _JSON_COLUMNS:
            row[key] = _as_json(data.get(java_key))
        else:
            row[key] = data.get(java_key)
    return row


class JavaOrderTool(BaseTool):
    name = "query_order"
    description = "查询订单信息，需要订单ID"
    args_model = QueryOrderArgs

    def __init__(self, client: JavaServiceClient, config: dict | None = None) -> None:
        super().__init__(config)
        self._client = client

    def _run(self, order_id: str = "", **kwargs) -> dict:
        if not order_id:
            raise ValueError("缺少 order_id")
        # Java notFound 也返回 HTTP 200（ApiResponse.success=false + code=NOT_FOUND），
        # 按 body 的 success 字段判定，不看状态码
        body = self._client.get_json(
            f"/api/orders/{quote(str(order_id), safe='')}",
            timeout_s=self.timeout_ms / 1000.0,
        )
        if not body.get("success"):
            return {
                "found": False,
                "order_id": order_id,
                "message": body.get("message") or "订单不存在",
            }
        return {"found": True, "order": _normalize(body.get("data") or {}, _ORDER_FIELD_MAP)}


class JavaLogisticsTool(BaseTool):
    name = "query_logistics"
    description = "查询物流状态，需要物流单号"
    args_model = QueryLogisticsArgs

    def __init__(self, client: JavaServiceClient, config: dict | None = None) -> None:
        super().__init__(config)
        self._client = client

    def _run(self, tracking_no: str = "", order_id: str = "", **kwargs) -> dict:
        if tracking_no:
            path = f"/api/logistics/{quote(str(tracking_no), safe='')}"
        elif order_id:
            path = f"/api/logistics/order/{quote(str(order_id), safe='')}"
        else:
            raise ValueError("缺少 tracking_no 或 order_id")
        body = self._client.get_json(path, timeout_s=self.timeout_ms / 1000.0)
        if not body.get("success"):
            return {
                "found": False,
                "tracking_no": tracking_no or order_id,
                "message": body.get("message") or "暂无物流信息",
            }
        return {
            "found": True,
            "logistics": _normalize(body.get("data") or {}, _LOGISTICS_FIELD_MAP),
        }
