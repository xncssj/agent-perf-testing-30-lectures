"""Multi-Agent 子代理层（v2）：身份继承、span 关联与根预算分配。

身份模型（共享 Harness 设计说明 第 8.1 节）：
- Supervisor 持有根 RunContext（run_id 为根）；
- Worker 继承 task_id、batch_id、根 run_id，每次调用额外产生 span_id，
  parent_span_id = 根 run_id（阶段二为单层扇出）；
- 工具尝试由根 Budget.allocate_tool_attempt 原子分配，Worker 不各自获得完整预算；
- 队列时间（等待调度）、Worker 执行时间与模型用量分别记录，根运行只汇总一次。
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

import time

from harness.contracts import RunContext, ToolCall, new_id
from harness.metrics import observe_usage_context
from harness.tool_executor import ToolExecutor

from .provider import BaseMultiAgentProvider


@dataclass
class AgentCallInfo:
    """一次子 Agent 调用的结构化记录（v2：携带身份与 span 关联）。

    started_at_wall/ended_at_wall 为本次调用的真实起止墙钟（006 R02），
    由调用方在重试/熔断路径同样如实填写；tool_call_id 关联本次调用的
    工具尝试，供导出器建立 工具→子Agent 的真实父子关系。
    """

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
    # v2 身份与 span 关联：继承根运行身份，每次调用产生独立 span
    span_id: str = ""
    parent_span_id: str = ""
    task_id: str = ""
    run_id: str = ""
    batch_id: str = ""
    # 006 R02：真实起止与工具尝试关联
    started_at_wall: float = 0.0
    ended_at_wall: float = 0.0
    tool_call_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class ProfessionalAgent:
    """子 Agent（v2）：任务决策 + 工具执行（经共享有界执行器）。

    与 v1 的差异：
    - 决策与执行异步化，工具经共享 ToolExecutor（Schema 校验/超时/重试/
      有界线程池），工具尝试由根预算原子分配；
    - 每次调用产生 span_id 并继承根运行身份；
    - 超时与重试仍由 Supervisor 编排层负责（本类不阻塞重试逻辑）。
    """

    def __init__(self, name: str, role: str, provider: BaseMultiAgentProvider,
                 executor: ToolExecutor, tool: str = "",
                 context_window_tokens: int = 0) -> None:
        self.name = name
        self.role = role
        self.provider = provider
        self.executor = executor
        self.tool = tool
        # 上下文窗口占用率指标的分母（0 表示不观测）
        self.context_window_tokens = int(context_window_tokens)

    async def run(self, query: str, ctx: RunContext, budget, recorder) -> AgentCallInfo:
        """执行一次任务：LLM 决策 →（有工具时）经执行器执行工具。

        recorder：根运行的 RunRecorder（事件与用量统一登记）；
        budget：根运行预算（工具尝试原子分配，含自动重试）。
        """
        info = AgentCallInfo(
            name=self.name, role=self.role,
            span_id=new_id(), parent_span_id=ctx.run_id,
            task_id=ctx.task_id, run_id=ctx.run_id, batch_id=ctx.batch_id,
        )
        info.started_at_wall = time.time()
        t0 = time.perf_counter()

        # 1) 子 Agent 独立任务决策（一次模型调用）
        task = await self.provider.agent_task(self.name, query, ctx)
        info.decision_ms = round(task.latency_ms, 3)
        info.tokens = task.usage.total_tokens or 0
        observe_usage_context(task.usage, self.context_window_tokens)
        recorder.record_model_usage(
            f"agent_task:{self.name}", task.model, task.latency_ms, task.usage,
            parent_span_id=info.span_id,
        )

        if task.tool is None:
            # 无工具决策（参数缺失需澄清 / 无需工具）：仅记录决策
            info.status = "no_tool"
            info.total_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            info.ended_at_wall = time.time()
            return info

        # 2) 工具执行：经共享执行器（校验/预算/超时/重试/事件）
        info.tool = task.tool
        info.args = task.args
        call = ToolCall(name=task.tool, arguments=dict(task.args or {}))
        info.tool_call_id = call.id
        obs = await self.executor.run(call, ctx, budget, recorder,
                                      parent_span_id=info.span_id)
        info.tool_ms = round(obs.duration_ms, 3)
        info.args = dict(task.args or {})
        if obs.rejected:
            info.status = "error"
            info.error = obs.error
        elif obs.error:
            info.status = "timeout" if obs.timed_out else "error"
            info.error = obs.error
        else:
            info.status = "success"
            info.result = obs.data or {}
        info.total_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        info.ended_at_wall = time.time()
        return info


class Workforce:
    """专业子 Agent 工作池：按名称获取子 Agent。"""

    AGENTS: list[tuple[str, str, str]] = [
        ("order_agent", "订单查询", "query_order"),
        ("logistics_agent", "物流查询", "query_logistics"),
        ("knowledge_agent", "知识库检索", "search_knowledge"),
        ("validation_agent", "事实校验", ""),
    ]

    def __init__(self, provider: BaseMultiAgentProvider, executor: ToolExecutor,
                 context_window_tokens: int = 0) -> None:
        self.provider = provider
        self.executor = executor
        self._agents: dict[str, ProfessionalAgent] = {
            name: ProfessionalAgent(name, role, provider, executor, tool,
                                    context_window_tokens=context_window_tokens)
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


def to_tool_used(agent_infos: list[AgentCallInfo]) -> dict:
    """把子 Agent 调用结果按工具名聚合成案例一兼容的 tool_used 结构。"""
    tool_used: dict = {}
    for info in agent_infos:
        if info.tool and info.status in ("success", "timeout") and info.result:
            tool_used[info.tool] = info.result
    return tool_used