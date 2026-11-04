"""Provider 边界：统一异步模型接入契约。

Harness 只消费本模块定义的类型，不解析具体供应商协议（共享 Harness 设计说明 第 6.1 节）：
- decide(messages, tools, context)：返回结构化决策及用量；
  原生工具协议与 JSON 兼容协议的转换都留在 Provider 内。
- stream_answer(messages, context)：输出文本增量、用量或失败。

消息与决策等公共类型统一定义在 harness.contracts，本模块做转出口，
保持 `from ..llm.base import Message` 等既有引用可用。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from .contracts import (  # noqa: F401  转出口
    AnswerChunk,
    Decision,
    Message,
    ModelUsage,
    RunContext,
)

# 原生模式下的控制函数：无业务副作用，表达结束/澄清/人工处理建议意图
CONTROL_TOOL_NAMES = ("finish", "ask_clarification", "complaint")

# 控制函数的 JSON Schema（原生模式提交给模型）
CONTROL_TOOL_SCHEMAS: dict[str, dict] = {
    "finish": {
        "description": "信息已足够，结束工具调用并生成最终答复",
        "parameters": {"type": "object", "properties": {}},
    },
    "ask_clarification": {
        "description": "用户问题不明确，需要向用户追问订单号或物流单号",
        "parameters": {"type": "object", "properties": {}},
    },
    "complaint": {
        "description": "用户表达投诉，需要建议联系人工客服",
        "parameters": {"type": "object", "properties": {}},
    },
}


class ProviderError(Exception):
    """Provider 层失败（网络、非 2xx、协议不支持等），由运行器映射为
    failed/provider_error 终态。不做透明重试，避免未记录的重复调用。"""


class InvalidDecisionError(Exception):
    """模型响应无法转换为合法决策（非法 JSON、混合控制动作与调用等），
    由运行器映射为 failed/invalid_decision 终态。"""


class BaseProvider(ABC):
    """异步 Provider 基类。"""

    model: str = ""

    @abstractmethod
    async def decide(
        self,
        messages: list[Message],
        tools: dict[str, dict],
        context: RunContext,
    ) -> Decision:
        """一次工具决策调用。tools 为业务工具名 -> JSON Schema。"""

    @abstractmethod
    def stream_answer(
        self,
        messages: list[Message],
        context: RunContext,
    ) -> AsyncIterator[AnswerChunk]:
        """最终答复流式生成：产出文本增量，最后一个增量携带用量（如可用）。"""

    @abstractmethod
    async def aclose(self) -> None:
        """释放连接资源；进程结束时调用。"""
