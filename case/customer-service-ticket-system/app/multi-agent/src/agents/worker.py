"""专业子 Agent 工作池：每位子 Agent 独立决策并执行其对应工具。

Multi-Agent 相对 Single-Agent 的核心差异：
- 子 Agent 先做一次独立的 LLM 任务决策（agent_task），再执行工具；
- 因此每笔业务会放大 LLM 调用次数（超管规划 + 各子 Agent 决策 + 校验 +
  答复生成），调用放大是 token 成本与容量开销上升的直接来源。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..tools.registry import ToolRegistry


@dataclass
class AgentCallInfo:
    """一次子 Agent 调用的结构化记录。"""

    name: str
    role: str = ""
    status: str = "success"  # success / no_tool / timeout / error / skipped_breaker
    tool: str = ""
    args: dict = field(default_factory=dict)
    decision_ms: float = 0.0
    tool_ms: float = 0.0
    total_ms: float = 0.0
    tokens: int = 0
    attempts: int = 1
    error: str | None = None
    result: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ProfessionalAgent:
    """子 Agent：任务决策 + 工具/校验执行。

    超时与重试由 Supervisor 在 invoke 层负责（本类不阻塞重试逻辑），
    但工具层自身仍有 BaseTool 的超时/异常兜底。
    """

    def __init__(self, name: str, role: str, llm, registry: ToolRegistry, tool: str = "") -> None:
        self.name = name
        self.role = role
        self.llm = llm
        self.registry = registry
        self.tool = tool

    def run(self, query: str, facts: dict | None = None) -> AgentCallInfo:
        """执行一次任务：先做本 Agent 的 LLM 决策，再执行工具/校验。"""
        info = AgentCallInfo(name=self.name, role=self.role)
        t0 = time.perf_counter()

        decision = self.llm.agent_task(self.name, query)
        info.decision_ms = round(decision.latency_ms, 3)
        info.tokens = decision.total_tokens

        if decision.tool_name is None:
            # 无工具决策（参数缺失 / 无需工具）：仅记录决策，不执行工具
            info.status = "no_tool"
            info.total_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return info

        info.tool = decision.tool_name
        info.args = decision.tool_args or {}
        tool_inst = self.registry.get(decision.tool_name)
        if tool_inst is None:
            info.status = "error"
            info.error = f"工具 {decision.tool_name} 未注册"
            info.total_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return info

        tool_out = tool_inst.execute(**info.args)
        info.tool_ms = round(tool_out.latency_ms, 3)
        if tool_out.timed_out:
            info.status = "timeout"
            info.error = tool_out.error
        elif tool_out.error:
            info.status = "error"
            info.error = tool_out.error
        else:
            info.status = "success"
            info.result = tool_out.result or {}
        info.total_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        return info


class Workforce:
    """专业子 Agent 工作池：按名称获取子 Agent。"""

    AGENTS: list[tuple[str, str, str]] = [
        ("order_agent", "订单查询", "query_order"),
        ("logistics_agent", "物流查询", "query_logistics"),
        ("knowledge_agent", "知识库检索", "search_knowledge"),
        ("validation_agent", "事实校验", ""),
    ]

    def __init__(self, llm, registry: ToolRegistry) -> None:
        self.llm = llm
        self.registry = registry
        self._agents: dict[str, ProfessionalAgent] = {
            name: ProfessionalAgent(name, role, llm, registry, tool)
            for name, role, tool in self.AGENTS
        }

    def get(self, name: str) -> ProfessionalAgent | None:
        return self._agents.get(name)

    def names(self) -> list[str]:
        return list(self._agents.keys())

    def describe_roles(self) -> str:
        lines = [
            f"{name}: {agent.role}" for name, agent in self._agents.items()
        ]
        return "\n".join(lines)


def to_tool_used(agent_infos: list[AgentCallInfo]) -> dict[str, Any]:
    """把子 Agent 调用结果按工具名聚合成案例一兼容的 tool_used 结构。"""
    tool_used: dict[str, Any] = {}
    for info in agent_infos:
        if info.tool and info.status in ("success", "timeout") and info.result:
            tool_used[info.tool] = info.result
    return tool_used