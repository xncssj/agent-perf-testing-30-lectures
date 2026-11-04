"""超管引擎（v2）：Multi-Agent 编排接入 Harness 身份、预算、事件与终态。

执行流程（与 02-architecture + 03-agent-design 对齐，保持编排管线结构，
不强行改造成 ReAct 循环——共享 Harness 设计说明 第 8.2 节原则同样适用于本阶段）：
    超管规划（意图 → Agent 任务清单，根预算一轮）
      → 并行扇出（Worker 继承根身份并产生 span；工具尝试由根预算原子分配）
      → 结果聚合 → 聚合校验（validation Agent）→ 答复生成（流式）

v2 关键口径（共享 Harness 设计说明 第 8.1 节）：
- Supervisor 持有根 RunContext；Worker 继承 task_id/batch_id/根 run_id 并产生
  span_id（parent_span_id = 根 run_id）；
- 工具尝试由同一根 Budget 原子分配，超预算立即终止扇出并进入终态；
- 队列时间、Worker 执行时间与模型用量分别记录（trace），根运行只汇总一次；
- 终态集合与单 Agent 一致：completed（answered/clarification/not_found/
  handoff_requested）/ failed / timed_out / budget_exhausted / cancelled；
  降级答复如实说明子服务不可用，不以兜底答复伪装成功。

可靠性红线（对应 04-communication 的容错参数，v1 语义保留）：
- 单个子 Agent 超时：settings.agent_timeout_ms（默认 3000ms）
- 失败重试：max_retries 次，指数退避；熔断后直接降级（skipped_breaker）
- 全流程总超时：request_timeout（deadline），超时以 timed_out 终态收敛
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import AsyncIterator, Callable, Optional

from harness.budget import Budget, BudgetExceeded, DeadlineExceeded
from harness.metrics import observe_context_occupancy, observe_usage_context
from harness.tokenization import token_count
from harness.contracts import (
    STOP_ANSWERED,
    STOP_CLARIFICATION,
    STOP_CLIENT_DISCONNECT,
    STOP_EMPTY_ANSWER,
    STOP_HANDOFF,
    STOP_NOT_FOUND,
    STOP_PROVIDER_ERROR,
    STOP_REQUEST_DEADLINE,
    STOP_TOOL_FAILED,
    Message,
    RunContext,
    RunInput,
    RunResult,
    new_id,
)
from harness.guardrail import guardrail
from harness.observation import RunRecorder
from harness.policy import (
    CLARIFY_TEXT,
    HANDOFF_TEXT,
    NOT_FOUND_TEXT,
)
from harness.providers import InvalidDecisionError, ProviderError
from harness.tool_executor import ToolExecutor

from ..agents.circuit_breaker import CircuitBreaker
from ..utils.logger import get_logger
from .provider import BaseMultiAgentProvider
from .worker import AgentCallInfo, Workforce, to_tool_used

logger = get_logger("mas.supervisor")


class SupervisorEngine:
    """Multi-Agent 超管执行引擎（v2）。"""

    def __init__(
        self,
        provider: BaseMultiAgentProvider,
        workforce: Workforce,
        executor: ToolExecutor,
        prompt_manager,
        settings,
    ) -> None:
        self.provider = provider
        self.workforce = workforce
        self.executor = executor
        self.prompt_manager = prompt_manager
        self.settings = settings
        self._breakers: dict[str, CircuitBreaker] = {}
        self._fault_hits: dict[str, int] = {}

    def _breaker(self, agent: str) -> CircuitBreaker:
        if agent not in self._breakers:
            self._breakers[agent] = CircuitBreaker(
                threshold=self.settings.breaker_threshold,
                window_ms=self.settings.breaker_window_ms,
            )
        return self._breakers[agent]

    # ---- 入口（与单 Agent HarnessRunner 相同的生成器契约）----
    async def run_stream(
        self,
        run_input: RunInput,
        internal_sink: Optional[Callable[[dict], None]] = None,
    ) -> AsyncIterator[dict]:
        """执行一次运行，逐个产出公共事件；最后一个事件为 done。"""
        task_id = run_input.task_id or new_id()
        timeout_s = float(self.settings.request_timeout)
        ctx = RunContext(
            task_id=task_id,
            run_id=new_id(),
            batch_id=run_input.batch_id,
            conversation_id=run_input.conversation_id,
            query=run_input.query,
            deadline=time.monotonic() + timeout_s,
        )
        budget = Budget(
            max_iterations=int(self.settings.max_iterations),
            max_tool_calls=int(self.settings.max_tool_calls),
            timeout_s=timeout_s,
        )
        queue: asyncio.Queue = asyncio.Queue()
        recorder = RunRecorder(ctx, queue=queue, internal_sink=internal_sink)
        started_epoch_ms = int(time.time() * 1000)

        async def business() -> RunResult:
            return await self._business(
                ctx, budget, recorder, run_input, started_epoch_ms
            )

        task = asyncio.create_task(business(), name=f"run_{ctx.run_id}")
        try:
            while True:
                get_task = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait(
                    {get_task, task}, return_when=asyncio.FIRST_COMPLETED
                )
                if get_task in done:
                    event = get_task.result()
                    yield event
                    if event.get("type") == "done":
                        await task
                        return
                else:
                    get_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await get_task
                    with contextlib.suppress(asyncio.QueueEmpty):
                        while True:
                            yield queue.get_nowait()
                    await task
                    return
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            raise
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def run(
        self,
        run_input: RunInput,
        internal_sink: Optional[Callable[[dict], None]] = None,
    ) -> RunResult:
        """非流式执行：消费同一运行流程，返回终态结果。"""
        done_payload: dict = {}
        async for event in self.run_stream(run_input, internal_sink=internal_sink):
            if event.get("type") == "done":
                done_payload = event
        return RunResult(**{
            k: v for k, v in done_payload.items() if k in RunResult.model_fields
        })

    # ---- 业务管线（独立任务中执行）----
    async def _business(
        self,
        ctx: RunContext,
        budget: Budget,
        recorder: RunRecorder,
        run_input: RunInput,
        started_epoch_ms: int,
    ) -> RunResult:
        response = ""
        intent = ""
        status = "failed"
        stop_reason = ""
        error: Optional[str] = None
        result_data: dict = {}  # mas 专属 trace 数据

        try:
            recorder.emit_thinking("超管正在理解问题并规划子 Agent 任务...")
            budget.begin_iteration()
            plan = await self._plan(run_input.query, ctx, recorder)
            intent = plan["intent"]
            result_data["plan_agents"] = plan["agents"]

            # 投诉意图：人工处理建议出口（只建议行为，不声称已转交）
            if intent == "complaint":
                status, stop_reason = "completed", "handoff_requested"
                response = HANDOFF_TEXT
                recorder.mark_evidence("template")
                recorder.emit_answer(response, source="template")
                result_data["plan_agents"] = plan["agents"]
                return self._finish(
                    ctx, budget, recorder, run_input, started_epoch_ms,
                    status, stop_reason, response, intent, error, result_data,
                )

            # 并行扇出：仅调度有数据任务的子 Agent
            data_agents = [n for n in plan["agents"] if n != "validation_agent"]
            agents: list[AgentCallInfo] = []
            fanout: dict = {}
            if data_agents:
                agents, fanout = await self._fan_out(
                    data_agents, run_input.query, ctx, budget, recorder
                )
                result_data["fanout"] = fanout

            tool_used = to_tool_used(agents)
            result_data["agents"] = agents

            # 聚合校验：派发了数据 Agent 就必须执行（校验 Agent 恒有执行记录）
            validation: dict = {}
            if data_agents:
                validation = await self._validate(
                    run_input.query, tool_used, ctx, recorder
                )
                result_data["validation"] = validation
                if "validation_agent" not in result_data["plan_agents"]:
                    result_data["plan_agents"] = result_data["plan_agents"] + [
                        "validation_agent"
                    ]

            # 降级标注
            degraded_agents = sorted({
                a.name for a in agents
                if a.status in ("timeout", "error", "skipped_breaker")
            })
            degraded = bool(degraded_agents) and self.settings.degradation_enabled
            result_data["degraded_agents"] = degraded_agents
            result_data["degraded"] = degraded

            # 出口判定（未解决的执行错误不能伪装成查询无结果或澄清）
            if not data_agents:
                status, stop_reason = "completed", "clarification"
                response = CLARIFY_TEXT
                recorder.mark_evidence("template")
                recorder.emit_answer(response, source="template")
                return self._finish(
                    ctx, budget, recorder, run_input, started_epoch_ms,
                    status, stop_reason, response, intent, error, result_data,
                )
            if not tool_used:
                if all(a.status == "no_tool" for a in agents):
                    status, stop_reason = "completed", "clarification"
                    response = CLARIFY_TEXT
                    recorder.mark_evidence("template")
                    recorder.emit_answer(response, source="template")
                    return self._finish(
                        ctx, budget, recorder, run_input, started_epoch_ms,
                        status, stop_reason, response, intent, error, result_data,
                    )
                if degraded:
                    # 降级开启：如实生成“子服务不可用”答复（trace 标注降级）
                    response = await self._generate(
                        ctx, recorder, run_input.query, tool_used, degraded_agents
                    )
                    if not response.strip():
                        raise _EmptyAnswer()
                    status, stop_reason = "completed", STOP_ANSWERED
                    return self._finish(
                        ctx, budget, recorder, run_input, started_epoch_ms,
                        status, stop_reason, response, intent, error, result_data,
                    )
                failed_names = sorted({
                    a.name for a in agents
                    if a.status in ("timeout", "error")
                })
                raise _ToolFailure(
                    "工具执行错误未解决: " + (", ".join(failed_names) or "unknown")
                )
            if all(
                not (tool_used.get(key) or {}).get("found")
                for key in ("query_order", "query_logistics", "search_knowledge")
                if tool_used.get(key)
            ):
                status, stop_reason = "completed", STOP_NOT_FOUND
                response = NOT_FOUND_TEXT
                recorder.mark_evidence("template")
                recorder.emit_answer(response, source="template")
                return self._finish(
                    ctx, budget, recorder, run_input, started_epoch_ms,
                    status, stop_reason, response, intent, error, result_data,
                )

            # 答复生成（流式）
            response = await self._generate(
                ctx, recorder, run_input.query, tool_used, degraded_agents
            )
            if not response.strip():
                raise _EmptyAnswer()
            status, stop_reason = "completed", STOP_ANSWERED
        except BudgetExceeded as exc:
            status, stop_reason = "budget_exhausted", exc.reason
        except DeadlineExceeded:
            status, stop_reason = "timed_out", "request_deadline"
        except _ToolFailure as exc:
            status, stop_reason, error = "failed", STOP_TOOL_FAILED, str(exc)
        except _EmptyAnswer:
            status, stop_reason, error = "failed", STOP_EMPTY_ANSWER, "答复为空"
        except InvalidDecisionError as exc:
            status, stop_reason, error = "failed", "invalid_decision", str(exc)
        except ProviderError as exc:
            status, stop_reason, error = "failed", STOP_PROVIDER_ERROR, str(exc)
        except asyncio.CancelledError:
            result = self._finish(
                ctx, budget, recorder, run_input, started_epoch_ms,
                "cancelled", STOP_CLIENT_DISCONNECT,
                response or recorder.answer_text, intent, error, result_data,
            )
            logger.warning(
                "运行取消 %s", json.dumps(result.model_dump(), ensure_ascii=False)
            )
            raise
        except Exception as exc:  # noqa: BLE001  未知异常按失败终态收敛
            status, stop_reason, error = "failed", "internal_error", str(exc)
            logger.exception("运行内部异常 run_id=%s", ctx.run_id)

        # 失败路径保留已输出的部分答复，但不改变终态
        response = response or recorder.answer_text
        return self._finish(
            ctx, budget, recorder, run_input, started_epoch_ms,
            status, stop_reason, response, intent, error, result_data,
        )

    # ---- 编排步骤 ----
    async def _plan(self, query: str, ctx: RunContext, recorder: RunRecorder) -> dict:
        try:
            plan = await asyncio.wait_for(
                self.provider.plan(query, ctx), timeout=ctx.remaining_s()
            )
        except asyncio.TimeoutError as exc:
            raise DeadlineExceeded() from exc
        observe_usage_context(
            plan.usage, int(getattr(self.settings, "context_window_tokens", 0))
        )
        recorder.record_model_usage(
            "supervisor_plan", plan.model, plan.latency_ms, plan.usage,
            parent_span_id=ctx.run_id,
        )
        recorder.emit_thinking(
            f"规划完成：意图 {plan.intent or '未知'}，"
            f"任务清单 {plan.agents or '[]'}"
        )
        return {"intent": plan.intent, "agents": list(plan.agents),
                "fallback": plan.fallback}

    async def _fan_out(self, agents: list[str], query: str, ctx: RunContext,
                       budget: Budget, recorder: RunRecorder):
        """并行扇出：熔断检查 → 故障注入 → 重试（指数退避）→ Worker 执行。

        根预算原子分配工具尝试；任一 Worker 触发预算/期限耗尽时取消其余任务。
        """
        limits = max(1, min(int(self.settings.parallel_limit), len(agents)))
        semaphore = asyncio.Semaphore(limits)
        t0 = time.perf_counter()

        async def invoke(name: str) -> AgentCallInfo:
            async with semaphore:
                return await self._invoke_agent(name, query, ctx, budget, recorder)

        tasks = {
            asyncio.create_task(invoke(name)): name
            for name in agents if self.workforce.get(name) is not None
        }
        results = await asyncio.gather(*tasks) if tasks else []
        for info in results:
            if isinstance(info, BaseException):
                raise info

        infos = [r for r in results if isinstance(r, AgentCallInfo)]
        # Worker 调用事件（tool_call/tool_result 已由执行器发出，这里补 Agent 维度）
        for a in infos:
            if a.tool:
                recorder.emit_agent_invoke(a.name, a.status, a.span_id, a.attempts)
        serial_sum = sum((a.total_ms or 0.0) for a in infos)
        round_wall = (time.perf_counter() - t0) * 1000.0
        fanout = {
            "round_wall_ms": round(round_wall, 3),
            "serial_sum_ms": round(serial_sum, 3),
            "efficiency": round(serial_sum / round_wall, 4) if round_wall else 0.0,
            "agents": len(infos),
        }
        return infos, fanout

    async def _invoke_agent(self, agent: str, query: str, ctx: RunContext,
                            budget: Budget, recorder: RunRecorder) -> AgentCallInfo:
        """单子 Agent 调度：熔断 → 故障注入 → 重试（指数退避）。"""
        t_invoke_start = time.time()
        breaker = self._breaker(agent)
        if breaker.is_open:
            remaining = breaker.state()["remaining_ms"]
            info = AgentCallInfo(
                name=agent, span_id=new_id(), parent_span_id=ctx.run_id,
                task_id=ctx.task_id, run_id=ctx.run_id, batch_id=ctx.batch_id,
                status="skipped_breaker",
                error=f"circuit open, remaining {remaining:.0f}ms",
            )
            info.started_at_wall = t_invoke_start
            info.ended_at_wall = time.time()
            recorder.emit_agent_invoke(
                agent, info.status, info.span_id, 1,
                start_wall=info.started_at_wall, end_wall=info.ended_at_wall,
                parent_span_id=info.parent_span_id, error_kind="breaker_open",
            )
            return info

        attempts = 0
        last_info: AgentCallInfo | None = None
        while attempts < int(self.settings.max_retries) + 1:
            attempts += 1
            t_attempt_start = time.time()
            try:
                await self._maybe_inject_fault(agent)
            except _FaultTimeout as exc:
                last_info = self._worker_info(agent, ctx, "timeout", str(exc),
                                              attempts)
                last_info.started_at_wall = t_attempt_start
                last_info.ended_at_wall = time.time()
            except Exception as exc:  # noqa: BLE001
                last_info = self._worker_info(agent, ctx, "error", str(exc),
                                              attempts)
                last_info.started_at_wall = t_attempt_start
                last_info.ended_at_wall = time.time()
            else:
                try:
                    info = await asyncio.wait_for(
                        self.workforce.get(agent).run(query, ctx, budget, recorder),
                        timeout=self.settings.agent_timeout_ms / 1000.0,
                    )
                except asyncio.TimeoutError:
                    last_info = self._worker_info(
                        agent, ctx, "timeout",
                        f"处理超时（agent_timeout "
                        f"{self.settings.agent_timeout_ms:.0f}ms）", attempts,
                    )
                    last_info.started_at_wall = t_attempt_start
                    last_info.ended_at_wall = time.time()
                except (BudgetExceeded, DeadlineExceeded):
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_info = self._worker_info(agent, ctx, "error", str(exc),
                                                  attempts)
                    last_info.started_at_wall = t_attempt_start
                    last_info.ended_at_wall = time.time()
                else:
                    info.attempts = attempts
                    last_info = info

            assert last_info is not None
            if last_info.status in ("success", "no_tool"):
                breaker.record_success()
                recorder.emit_agent_invoke(
                    agent, last_info.status, last_info.span_id, attempts,
                    start_wall=last_info.started_at_wall or None,
                    end_wall=last_info.ended_at_wall or None,
                    parent_span_id=last_info.parent_span_id,
                )
                return last_info
            if attempts <= int(self.settings.max_retries):
                backoff = self.settings.retry_backoff_ms * (2 ** (attempts - 1))
                await asyncio.sleep(backoff / 1000.0)

        breaker.record_failure()
        opened = breaker.state()["open"]
        last_info = last_info or self._worker_info(agent, ctx, "error", None, attempts)
        last_info.status = "skipped_breaker" if opened else last_info.status
        last_info.started_at_wall = last_info.started_at_wall or t_invoke_start
        last_info.ended_at_wall = last_info.ended_at_wall or time.time()
        recorder.emit_agent_invoke(
            agent, last_info.status, last_info.span_id, attempts,
            start_wall=last_info.started_at_wall or None,
            end_wall=last_info.ended_at_wall or None,
            parent_span_id=last_info.parent_span_id,
            error_kind="breaker_open" if opened else None,
        )
        return last_info

    @staticmethod
    def _worker_info(agent: str, ctx: RunContext, status: str,
                     error: str | None, attempts: int) -> AgentCallInfo:
        return AgentCallInfo(
            name=agent, status=status, error=error, attempts=attempts,
            span_id=new_id(), parent_span_id=ctx.run_id,
            task_id=ctx.task_id, run_id=ctx.run_id, batch_id=ctx.batch_id,
        )

    async def _maybe_inject_fault(self, agent: str) -> None:
        """按配置注入故障：支持按 Agent 或共享 LLM 注入 timeout/error/down。"""
        s = self.settings
        if not s.fault_enabled:
            return
        target_hit = s.fault_target in ("", agent) or (
            s.fault_target == "llm" and agent != "validation_agent"
        )
        if not target_hit:
            return
        counter = self._fault_hits.get(agent, 0)
        if s.fault_limit and counter >= s.fault_limit:
            return
        self._fault_hits[agent] = counter + 1
        if s.fault_mode == "timeout":
            await asyncio.sleep(s.fault_latency_ms / 1000.0)
            raise _FaultTimeout(f"处理超时（注入耗时 {s.fault_latency_ms:.0f}ms）")
        if s.fault_mode == "error":
            raise RuntimeError("子服务返回 5xx（注入）")
        if s.fault_mode == "down":
            raise ConnectionError("子服务不可用（注入）")

    async def _validate(self, query: str, tool_used: dict, ctx: RunContext,
                        recorder: RunRecorder) -> dict:
        facts = {
            "order": tool_used.get("query_order"),
            "logistics": tool_used.get("query_logistics"),
            "knowledge": tool_used.get("search_knowledge"),
        }
        try:
            verdict = await asyncio.wait_for(
                self.provider.validation(query, facts, ctx),
                timeout=ctx.remaining_s(),
            )
        except asyncio.TimeoutError as exc:
            raise DeadlineExceeded() from exc
        observe_usage_context(
            verdict.usage, int(getattr(self.settings, "context_window_tokens", 0))
        )
        recorder.record_model_usage(
            "validation_agent", verdict.model, verdict.latency_ms, verdict.usage,
            parent_span_id=ctx.run_id,
        )
        validation = {
            "status": verdict.status,
            "missing": list(verdict.missing),
            "latency_ms": round(verdict.latency_ms, 3),
            "tokens": verdict.usage.total_tokens or 0,
            "fallback": verdict.fallback,
        }
        recorder.emit_validation(validation)
        return validation

    async def _generate(self, ctx: RunContext, recorder: RunRecorder,
                        query: str, tool_used: dict,
                        degraded_agents: list[str]) -> str:
        """答复生成（流式）：超管汇总各 Agent 结果。此阶段禁用工具。"""
        recorder.mark_evidence(
            getattr(self.provider, "evidence_kind", "non_stream")
        )
        recorder.emit_thinking("正在整合各子 Agent 结果并生成答复...")
        facts = {
            "order": tool_used.get("query_order"),
            "logistics": tool_used.get("query_logistics"),
            "knowledge": tool_used.get("search_knowledge"),
        }
        system = self.prompt_manager.system_prompt(self.workforce.describe_roles())
        gen_prompt = (
            self.prompt_manager.raw("response_gen.txt")
            .replace("{user_query}", query)
            .replace("{order_info}", json.dumps(facts.get("order") or {}, ensure_ascii=False))
            .replace("{logistics_info}", json.dumps(facts.get("logistics") or {}, ensure_ascii=False))
        )
        messages = [
            Message(role="system", content=system),
            Message(
                role="tool",
                content=json.dumps(
                    {**facts,
                     "degraded": {"agents": degraded_agents} if degraded_agents else {}},
                    ensure_ascii=False,
                ),
            ),
            Message(role="user", content=gen_prompt),
        ]
        parts: list[str] = []
        remaining = ctx.remaining_s()
        if remaining <= 0:
            raise DeadlineExceeded()
        observe_context_occupancy(
            token_count("".join(m.content or "" for m in messages)),
            int(getattr(self.settings, "context_window_tokens", 0)),
        )
        try:
            async with asyncio.timeout(remaining):
                async for chunk in self.provider.stream_answer(messages, ctx):
                    if chunk.content:
                        recorder.emit_answer(chunk.content, source="model")
                        parts.append(chunk.content)
                    if chunk.usage is not None:
                        recorder.record_model_usage(
                            "final_answer", self.provider.model, 0.0, chunk.usage,
                            parent_span_id=ctx.run_id,
                        )
        except TimeoutError as exc:
            raise DeadlineExceeded() from exc
        return "".join(parts)

    # ---- 汇总 ----
    def _finish(
        self,
        ctx: RunContext,
        budget: Budget,
        recorder: RunRecorder,
        run_input: RunInput,
        started_epoch_ms: int,
        status: str,
        stop_reason: str,
        response: str,
        intent: str,
        error: Optional[str],
        result_data: dict,
    ) -> RunResult:
        counters = budget.counters()
        recorder.intent = intent
        e2e_ms = recorder.elapsed_ms()
        metrics = recorder.metrics(counters, e2e_ms)
        trace = recorder.trace(counters)
        trace.update({
            "intent": intent,
            "plan_agents": result_data.get("plan_agents", []),
            "agents": [
                a.to_dict() for a in result_data.get("agents", [])
            ],
            "fanout": {
                k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in (result_data.get("fanout") or {}).items()
            },
            "validation": result_data.get("validation", {}),
            "degraded": result_data.get("degraded", False),
            "degraded_agents": result_data.get("degraded_agents", []),
            "tool_calls": [
                {
                    "tool": a.tool,
                    "args": a.args,
                    "tool_call_id": a.tool_call_id,
                    "latency_ms": a.tool_ms,
                    "timed_out": a.status == "timeout",
                    "error": a.error,
                    "result": a.result,
                    "agent": a.name,
                    "status": a.status,
                    "span_id": a.span_id,
                    "parent_span_id": a.parent_span_id,
                    "started_at_wall": a.started_at_wall,
                    "ended_at_wall": a.ended_at_wall,
                }
                for a in result_data.get("agents", []) if a.tool
            ],
            "model_calls": getattr(recorder, "model_calls", 0),
        })
        # 阶段二评测脚本按 v1 兼容方式读取 metrics["trace"]（与顶层 trace 同一结构）
        metrics["trace"] = trace
        # 安全状态逐笔采集（第 19 讲安全门禁口径）：Multi-Agent 不经共享 runtime，
        # 在此对最终答复做与阶段一/三同一实现的护栏检查（共享包 harness/guardrail.py）
        metrics["guardrail"] = guardrail.check(response)
        result = RunResult(
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            batch_id=ctx.batch_id,
            status=status,
            stop_reason=stop_reason,
            response=response,
            intent=intent,
            error=error,
            started_at_epoch_ms=started_epoch_ms,
            ended_at_epoch_ms=int(time.time() * 1000),
            metrics=metrics,
            trace=trace,
        )
        recorder.emit_done({
            "status": result.status,
            "stop_reason": result.stop_reason,
            "response": result.response,
            "intent": result.intent,
            "error": result.error,
            "metrics": result.metrics,
            "trace": result.trace,
        })
        logger.info(
            "运行终态 %s", json.dumps({
                "run_id": result.run_id, "task_id": result.task_id,
                "batch_id": result.batch_id, "status": result.status,
                "stop_reason": result.stop_reason,
                "degraded": result.trace.get("degraded"),
                "total_tokens": result.metrics.get("total_tokens"),
                "evidence_kind": result.metrics.get("evidence_kind"),
            }, ensure_ascii=False)
        )
        return result


class _ToolFailure(Exception):
    """未解决的工具失败：映射为 failed/tool_failed。"""


class _EmptyAnswer(Exception):
    """最终答复为空：映射为 failed/empty_answer。"""


class _FaultTimeout(TimeoutError):
    pass