"""LLM 客户端抽象。

设计目标：
1. 默认使用 MockLlm（确定性、无密钥），在无外部 LLM 环境下也能跑通性能基线，
   且结果完全可复现（固定 seed）。
2. 接入真实模型时使用 OpenAILlm（OpenAI 兼容端点，支持 DeepSeek 等），
   通过环境变量 LLM_PROVIDER=openai 切换。

每次 LLM 调用都返回结构化结果（内容、工具决策、token 用量、耗时），
为 TTFT / TPOT / 成本 / Trace 采集提供统一数据。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator


@dataclass
class Message:
    """一次 LLM 调用中的单条消息。"""

    role: str  # system / user / assistant / tool
    content: str


@dataclass
class LLMResult:
    """一次 LLM 调用的结构化结果。"""

    content: str = ""
    tool_name: str | None = None
    tool_args: dict = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0  # 本次调用总耗时
    ttft_ms: float = 0.0  # 首 token 延迟
    tpots_ms: list[float] = field(default_factory=list)  # 每个 token 间隔
    streamed: bool = False
    model: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def usage(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class StreamEvent:
    """流式输出中的一个事件。"""

    kind: str  # thinking / tool_call / tool_result / answer / done
    content: str = ""
    tool: str = ""
    args: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    token: str = ""
    usage: dict = field(default_factory=dict)


class BaseLLMClient(ABC):
    """LLM 客户端基类，接口固定两种模式：完整调用与流式调用。"""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.model = settings.model_name

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """非流式调用，返回完整结果。"""

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """流式调用，逐步产出文本片段（token）。"""

    def stream_sync(
        self,
        messages: list[Message],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """同步流式默认实现：整段取回后按片段切分产出。

        注意：默认实现不是真实流式（无逐 token 延迟），TTFT/TPOT 不具备
        真实证据效力；子类应覆写（如 MockLlm 按 token 节奏产出）。
        """
        result = self.complete(messages, temperature, max_tokens)
        content = result.content or ""
        for i in range(0, len(content), 2):
            yield content[i : i + 2]


def ms(t0: float) -> float:
    """返回自 t0 以来的毫秒耗时。"""
    return (time.perf_counter() - t0) * 1000.0