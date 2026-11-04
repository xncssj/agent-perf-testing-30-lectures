"""订单查询工具：按订单号查询订单快照。"""
from __future__ import annotations

from ..tools.base import BaseTool


class OrderTool(BaseTool):
    name = "query_order"
    description = "查询订单信息，需要订单ID"

    def __init__(self, repo, config: dict | None = None) -> None:
        super().__init__(config)
        self._repo = repo

    def _run(self, order_id: str = "", **kwargs) -> dict:
        if not order_id:
            raise ValueError("缺少 order_id")
        order = self._repo.get_order(order_id)
        if order is None:
            return {"found": False, "order_id": order_id, "message": "订单不存在"}
        return {"found": True, "order": order}