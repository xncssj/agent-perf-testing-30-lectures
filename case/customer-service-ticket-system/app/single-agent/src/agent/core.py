"""Single Agent 兼容入口：保留 v1 引擎外观，内部委托 Harness 运行器。

v2 起唯一的规划循环在 harness.runtime（共享 Harness 设计说明 第 3.2、5.3 节）；
本模块不再包含第二套规划循环，仅提供：
- execute(query)：同步兼容包装，仅供没有运行中事件循环的调用方；
- execute_stream(query)：同步生成器桥接（有界队列），消费者关闭时传递取消；
- arun(query)：异步调用方入口。

时间口径说明（v2）：
- 片段级计时（first_answer_ms / chunk_intervals_ms）为实测值；
- TTFT/TPOT 不再由配置或片段数合成，保留字段置 None，避免冒充 token 证据。
"""
from __future__ import annotations

import asyncio
import queue as queue_mod
import threading
from dataclasses import dataclass, field

from ..harness.contracts import RunInput, RunResult
from ..harness.runtime import HarnessRunner
from ..harness.tool_executor import ToolExecutor
from ..models.schemas import ToolCallInfo


@dataclass
class StepTiming:
    kind: str  # planning / tool / generation
    name: str = ""
    latency_ms: float = 0.0
    detail: str = ""


@dataclass
class AgentResult:
    task_id: str
    run_id: str
    query: str
    intent: str = ""
    response: str = ""
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    steps: list[StepTiming] = field(default_factory=list)
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    ttft_ms: float | None = None
    tpots_ms: list[float] = field(default_factory=list)
    e2e_ms: float = 0.0
    plan_latency_ms: float = 0.0
    status: str = "completed"
    stop_reason: str = ""

    def metrics(self, with_trace: bool = True) -> dict:
        """v2 指标：ttft/tpot 为 None（不合成），片段级计时见 metrics 字段。"""
        base = {
            "ttft_ms": None,
            "tpot_ms": None,
            "e2e_ms": round(self.e2e_ms, 3),
            "token_count": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
        if with_trace:
            base["trace"] = {
                "intent": self.intent,
                "plan_latency_ms": round(self.plan_latency_ms, 3),
                "tool_calls": [t.model_dump() for t in self.tool_calls],
                "steps": [
                    {
                        "kind": s.kind,
                        "name": s.name,
                        "latency_ms": round(s.latency_ms, 3),
                        "detail": s.detail,
                    }
                    for s in self.steps
                ],
            }
        return base


class SingleAgentEngine:
    """v1 兼容外观：装配执行器与 Harness 运行器，映射结果结构。"""

    def __init__(self, provider, registry, prompt_manager, settings) -> None:
        self.provider = provider
        self.registry = registry
        self.prompt_manager = prompt_manager
        self.settings = settings
        self.executor = ToolExecutor(
            registry,
            tool_workers=settings.tool_workers,
            tool_queue_size=settings.tool_queue_size,
        )
        self.runner = HarnessRunner(provider, self.executor, settings, prompt_manager)

    # ---- 同步兼容 ----
    def execute(self, query: str) -> AgentResult:
        """同步执行一次完整业务。运行中事件循环内请使用 arun()。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "当前线程已有事件循环：请使用 await engine.arun(query) 异步入口"
            )
        result = asyncio.run(self.arun(query))
        return result

    async def arun(self, query: str) -> AgentResult:
        """异步执行一次完整业务（与 execute 共用同一运行流程）。"""
        run_result = await self.runner.run(RunInput(query=query))
        return _to_agent_result(run_result, query)

    def execute_stream(self, query: str, run_id: str | None = None):
        """同步生成器桥接：逐事件产出（thinking/tool_call/tool_result/answer/done）。

        通过有界队列桥接异步运行器；消费者关闭生成器时传递取消，
        不重新实现第二套规划循环。
        """
        out: queue_mod.Queue = queue_mod.Queue(maxsize=256)
        stop = threading.Event()
        loop = asyncio.new_event_loop()

        async def _drive():
            try:
                async for event in self.runner.run_stream(RunInput(query=query)):
                    if stop.is_set():
                        raise asyncio.CancelledError()
                    out.put(event)
            except asyncio.CancelledError:
                pass
            finally:
                out.put(None)

        def _worker():
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_drive())
            finally:
                loop.close()
                out.put(None)

        thread = threading.Thread(target=_worker, daemon=True, name="engine-stream")
        thread.start()
        try:
            while True:
                try:
                    event = out.get(timeout=0.1)
                except queue_mod.Empty:
                    continue
                if event is None:
                    break
                yield event
        finally:
            stop.set()
            thread.join(timeout=5)

    # ---- 生命周期 ----
    async def aclose(self) -> None:
        await self.provider.aclose()
        await self.executor.aclose()


def _to_agent_result(result: RunResult, query: str) -> AgentResult:
    """RunResult -> v1 兼容 AgentResult 结构。"""
    trace = result.trace or {}
    tool_calls = [
        ToolCallInfo(
            tool=obs.get("tool", ""),
            args=obs.get("args") or {},
            latency_ms=obs.get("duration_ms", 0.0),
            # 总尝试次数：共享执行器在观察里回填（超时自动重试时 > 1），
            # 供"重试增加"信号与调用放大系数回填（第 06、07 讲口径）
            attempts=int(obs.get("attempt", 1) or 1),
            timed_out=obs.get("error_kind") == "timeout",
            error=obs.get("error"),
            result=obs.get("data") or {},
        )
        for obs in trace.get("tool_calls", [])
    ]
    steps = [
        StepTiming(kind="tool", name=obs.get("tool", ""),
                   latency_ms=obs.get("duration_ms", 0.0))
        for obs in trace.get("tool_calls", [])
    ]
    answer_summary = trace.get("answer_summary") or {}
    if answer_summary.get("chunks"):
        steps.append(StepTiming(kind="generation", name="response_gen"))
    metrics = result.metrics or {}
    usage = metrics.get("usage") or {}
    return AgentResult(
        task_id=result.task_id,
        run_id=result.run_id,
        query=query,
        intent=result.intent,
        response=result.response,
        tool_calls=tool_calls,
        steps=steps,
        total_tokens=metrics.get("total_tokens"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=metrics.get("answer_usage", {}).get("completion_tokens"),
        ttft_ms=None,
        tpots_ms=[],
        e2e_ms=metrics.get("e2e_ms", 0.0),
        status=result.status,
        stop_reason=result.stop_reason,
    )
