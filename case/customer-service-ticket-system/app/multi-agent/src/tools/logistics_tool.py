"""物流查询工具：按物流单号或订单号查询物流快照。"""
from __future__ import annotations

from ..tools.base import BaseTool


class LogisticsTool(BaseTool):
    name = "query_logistics"
    description = "查询物流状态，需要物流单号"

    def __init__(self, repo, config: dict | None = None) -> None:
        super().__init__(config)
        self._repo = repo

    def _run(self, tracking_no: str = "", order_id: str = "", **kwargs) -> dict:
        if tracking_no:
            row = self._repo.get_logistics_by_tracking(tracking_no)
        elif order_id:
            row = self._repo.get_logistics_by_order(order_id)
        else:
            raise ValueError("缺少 tracking_no 或 order_id")

        if row is None:
            return {
                "found": False,
                "tracking_no": tracking_no or order_id,
                "message": "暂无物流信息",
            }
        return {"found": True, "logistics": row}