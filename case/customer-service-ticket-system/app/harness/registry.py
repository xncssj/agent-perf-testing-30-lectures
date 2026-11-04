"""工具注册中心：只负责定义与查找；执行控制统一在 Harness 工具执行器。

enabled=false 的工具不对外描述、不进入 Provider 工具定义、不可执行。
工具对象为鸭子类型：需具备 name / description / timeout_ms / retry /
execute(**kwargs)（可选 args_model 参数模型），各阶段的业务工具实现均可注册。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RegisteredTool(Protocol):
    """注册表对工具对象的最小要求（结构化类型，不绑定具体基类）。"""

    name: str
    description: str

    def execute(self, **kwargs: Any) -> Any: ...


@dataclass
class ToolEntry:
    tool: RegisteredTool
    enabled: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(self, tool: RegisteredTool, enabled: bool = True) -> None:
        if tool.name in self._entries:
            raise ValueError(f"重复注册工具: {tool.name}")
        self._entries[tool.name] = ToolEntry(tool=tool, enabled=enabled)

    def get_entry(self, name: str) -> ToolEntry | None:
        return self._entries.get(name)

    def get(self, name: str) -> RegisteredTool | None:
        entry = self._entries.get(name)
        return entry.tool if entry else None

    def names(self) -> list[str]:
        return list(self._entries.keys())

    def enabled_names(self) -> list[str]:
        return [n for n, e in self._entries.items() if e.enabled]

    def describe(self) -> str:
        """仅描述启用的工具。"""
        lines = [
            f"{e.tool.name}: {e.tool.description}"
            for e in self._entries.values()
            if e.enabled
        ]
        return "\n".join(lines)
