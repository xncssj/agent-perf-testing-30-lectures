"""Multi-Agent 模型接入契约（v2）。

与单 Agent 的 ReAct Provider（harness.providers）不同，Multi-Agent 的模型调用
分四类：超管规划（plan）、子 Agent 任务决策（agent_task）、聚合校验（validation）
与答复生成（stream_answer）。本模块定义统一契约与结果类型，
协议实现见 mock_provider / openai_provider。

用量与耗时统一使用共享契约（harness.contracts 的 ModelUsage），
由运行器登记到 RunRecorder，根运行只汇总一次（共享 Harness 设计说明 第 8.1 节）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

from harness.contracts import AnswerChunk, ModelUsage, RunContext
from harness.providers import InvalidDecisionError, ProviderError  # noqa: F401  转出口


@dataclass
class PlanResult:
    """超管规划：意图 + 专业 Agent 任务清单。"""

    intent: str = ""
    agents: list[str] = field(default_factory=list)
    fallback: bool = False
    model: str = ""
    latency_ms: float = 0.0
    usage: ModelUsage = field(default_factory=ModelUsage)


@dataclass
class AgentTaskResult:
    """子 Agent 任务决策：工具调用意图（tool=None 表示无工具/需澄清）。"""

    tool: str | None = None
    args: dict = field(default_factory=dict)
    clarify: bool = False
    fallback: bool = False
    model: str = ""
    latency_ms: float = 0.0
    usage: ModelUsage = field(default_factory=ModelUsage)


@dataclass
class ValidationVerdict:
    """聚合校验结论：事实完整性（幂等检查，不虚构事实）。"""

    status: str = "complete"  # complete / incomplete
    missing: list[str] = field(default_factory=list)
    fallback: bool = False
    model: str = ""
    latency_ms: float = 0.0
    usage: ModelUsage = field(default_factory=ModelUsage)


class BaseMultiAgentProvider(ABC):
    """Multi-Agent 模型接入基类（异步契约）。"""

    model: str = ""
    evidence_kind = "non_stream"

    @abstractmethod
    async def plan(self, query: str, context: RunContext) -> PlanResult:
        """超管规划：识别意图并分解专业 Agent 任务清单。"""

    @abstractmethod
    async def agent_task(self, agent: str, query: str,
                         context: RunContext) -> AgentTaskResult:
        """子 Agent 任务决策：产出其对工具的调用决策（或无工具/澄清）。"""

    @abstractmethod
    async def validation(self, query: str, facts: dict,
                         context: RunContext) -> ValidationVerdict:
        """聚合校验：检查聚合事实完整性（确定性、不可注入错误的幂等检查）。"""

    @abstractmethod
    def stream_answer(self, messages: list, context: RunContext) -> AsyncIterator[AnswerChunk]:
        """答复生成：超管汇总各 Agent 结果流式产出自然语言答复。

        messages 约定：[system, tool(聚合事实 JSON), user(生成指令)]，
        与单 Agent 的生成消息结构一致，便于共享 Mock 答复逻辑。
        """

    @abstractmethod
    async def aclose(self) -> None:
        """释放连接资源；进程结束时调用。"""