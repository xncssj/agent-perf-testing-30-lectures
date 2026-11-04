"""工具注册中心：按名称获取工具实例，供 Agent 引擎动态选择。"""
from __future__ import annotations

from .base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def describe(self) -> str:
        lines = [f"{t.name}: {t.description}" for t in self._tools.values()]
        return "\n".join(lines)