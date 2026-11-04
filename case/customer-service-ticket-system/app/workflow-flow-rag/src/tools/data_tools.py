"""订单查询工具：从只读数据快照返回订单信息。"""
from __future__ import annotations

from ..models.store import DataStore
from .base import BaseTool


class QueryOrderTool(BaseTool):
    name = "query_order"
    description = "按订单号查询订单基本信息"

    def __init__(self, store: DataStore, config: dict | None = None) -> None:
        super().__init__(config)
        self._store = store

    def _run(self, order_id: str, **kwargs) -> dict:
        order = self._store.get_order(str(order_id))
        if order is None:
            return {"found": False, "error": f"订单 {order_id} 不存在"}
        return {"found": True, "order": order}


class QueryLogisticsTool(BaseTool):
    name = "query_logistics"
    description = "按订单号或物流单号查询物流轨迹"

    def __init__(self, store: DataStore, config: dict | None = None) -> None:
        super().__init__(config)
        self._store = store

    def _run(self, order_id: str | None = None, tracking_no: str | None = None, **kwargs) -> dict:
        logistics = None
        key = ""
        if tracking_no:
            logistics = self._store.get_logistics_by_tracking(str(tracking_no))
            key = f"运单 {tracking_no}"
        elif order_id:
            logistics = self._store.get_logistics_by_order(str(order_id))
            key = f"订单 {order_id}"
        if logistics is None:
            return {"found": False, "error": f"{key} 无物流轨迹"}
        return {"found": True, "logistics": logistics}