"""Prompt 管理器：加载并渲染 src/prompts 下的模板。

渲染使用占位符直接替换而非 str.format：模板中的 JSON 示例包含花括号，
str.format 会将其误解析为格式占位符导致 KeyError。
"""
from __future__ import annotations

from pathlib import Path


class PromptManager:
    def __init__(self, prompts_dir: Path) -> None:
        self.prompts_dir = prompts_dir

    def _load(self, name: str) -> str:
        fp = self.prompts_dir / name
        return fp.read_text(encoding="utf-8")

    def raw(self, name: str) -> str:
        """返回未渲染的模板原文（供 Harness 策略层自行替换占位符）。"""
        return self._load(name)

    def system_prompt(self, tools_description: str) -> str:
        """系统 Prompt：注入可用工具清单。"""
        return self._load("system_prompt.txt").replace(
            "{tools_description}", tools_description
        )

    def tool_selection(self, user_query: str) -> str:
        """工具选择 Prompt：要求模型输出 JSON 决策。"""
        return self._load("tool_selection.txt").replace("{user_query}", user_query)

    def response_gen(self, user_query: str, order_info: str, logistics_info: str) -> str:
        """答复生成 Prompt：注入订单与物流信息。"""
        return (
            self._load("response_gen.txt")
            .replace("{user_query}", user_query)
            .replace("{order_info}", order_info)
            .replace("{logistics_info}", logistics_info)
        )