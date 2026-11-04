"""工具注册表：按意图决定数据检索节点调用哪些工具（并行扇出）。"""
from __future__ import annotations

from ..models.store import DataStore
from .base import BaseTool, ToolResult
from .data_tools import QueryLogisticsTool, QueryOrderTool


class ToolRegistry:
    """订单/物流工具注册与调度（数据检索节点的扇出执行者）。

    与传统 Agent 的"模型选工具"不同，Workflow 由意图识别结果确定固定工具序列，
    是 Reducing Choice 降低不确定性的一种实现（docs/02）。
    """

    def __init__(self, store: DataStore, config: dict | None = None) -> None:
        cfg = config or {}
        self._tools: dict[str, BaseTool] = {
            QueryOrderTool.name: QueryOrderTool(store, cfg.get("query_order") or {}),
            QueryLogisticsTool.name: QueryLogisticsTool(store, cfg.get("query_logistics") or {}),
        }

    def call(self, name: str, **kwargs) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(result={}, error=f"未知工具 {name}")
        return tool.execute(**kwargs)

    def names(self) -> list[str]:
        return list(self._tools)

    def fanout(self, calls: list[dict]) -> list[dict]:
        """并行执行一组调用，返回带耗时的结果列表（进程内串行调度）。"""
        results = []
        for call in calls:
            name = call["name"]
            kwargs = {k: v for k, v in call.items() if k != "name"}
            res = self.call(name, **kwargs)
            results.append({
                "tool": name,
                "params": kwargs,
                "found": bool((res.result or {}).get("found")),
                "result": res.result,
                "latency_ms": res.latency_ms,
                "error": res.error,
            })
        return results