"""Harness 单一异步运行流程（v2）。

一次运行只经过本模块的循环（共享 Harness 设计说明 第 5 节）：
1. 接受任务，生成 run_id、起始时间与 deadline，输出进度事件；
2. Provider 返回结构化决策，累加用量并检查决策类型；
3. 每次工具实际执行前检查预算，先发 tool_call 再发 tool_result（事件即时产出，
   不缓冲到轮次结束）；
4. 模型消息与观察按调用 ID 回填历史，进入下一轮决策；
5. finalize 后进入最终答复阶段（禁用工具），澄清/无结果/人工处理建议
   由客服策略确定出口；
6. 答复增量产生 answer 事件，汇总一次结果并产出唯一终态。

实现结构：业务循环运行在独立任务中，事件经有界内部队列交给
run_stream 逐个产出；消费端断连时取消业务任务，迟到工具结果
不会覆盖已登记的取消终态。

终态口径：completed 允许带 answered/clarification/not_found/handoff_requested；
failed / timed_out / budget_exhausted / cancelled 均为不合格，不以兜底答复
伪装成功；未解决的执行错误不能转换成“查询无结果”。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from typing import AsyncIterator, Callable, Optional

# Python 3.10 兼容性补丁：实现真正能抛出 TimeoutError 的 asyncio.timeout
if sys.version_info < (3, 11):
    from concurrent.futures import TimeoutError as _SyncTimeoutError

    class _AsyncTimeoutContext:
        """asyncio.timeout 的 Python 3.10 回退实现。"""
        def __init__(self, delay: float) -> None:
            self._delay = delay
            self._task: asyncio.Task | None = None
            self._timeout_handler: asyncio.TimerHandle | None = None

        async def __aenter__(self):
            self._task = asyncio.current_task()
            if self._task is None or self._delay <= 0:
                return self
            loop = asyncio.get_event_loop()
            self._timeout_handler = loop.call_later(
                self._delay, self._cancel_task
            )
            return self

        def _cancel_task(self) -> None:
            if self._task is not None and not self._task.done():
                self._task.cancel(msg=f"Timeout after {self._delay}s")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if self._timeout_handler is not None:
                self._timeout_handler.cancel()
                self._timeout_handler = None
            # 如果任务被取消且是我们的超时导致的，转换为 TimeoutError
            if exc_type is asyncio.CancelledError and self._task is not None:
                # 检查是否是我们的超时取消的（通过消息标记）
                if self._task.cancelling() > 0:
                    raise TimeoutError(f"Operation timed out after {self._delay}s")
            return False

    asyncio.timeout = _AsyncTimeoutContext  # type: ignore[assignment]

from . import policy as cs_policy
from .guardrail import guardrail
from .metrics import observe_context_occupancy
from .providers import BaseProvider, InvalidDecisionError, ProviderError
from .logging_utils import get_logger
from .budget import Budget, BudgetExceeded, DeadlineExceeded
from .tokenization import token_count
from .contracts import (
    STOP_ANSWERED,
    STOP_CLARIFICATION,
    STOP_CLIENT_DISCONNECT,
    STOP_EMPTY_ANSWER,
    STOP_HANDOFF,
    STOP_INVALID_DECISION,
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
from .observation import RunRecorder
from .tool_executor import ToolExecutor

logger = get_logger("harness.runtime")


class _ToolFailure(Exception):
    """未解决的工具失败：映射为 failed/tool_failed。"""


class _EmptyAnswer(Exception):
    """最终答复为空：映射为 failed/empty_answer。"""


class HarnessRunner:
    """单次业务运行的异步执行器（由服务层组装，跨运行共享依赖）。"""

    def __init__(
        self,
        provider: BaseProvider,
        executor: ToolExecutor,
        settings,
        prompt_manager=None,
    ) -> None:
        self.provider = provider
        self.executor = executor
        self.settings = settings
        self.prompt_manager = prompt_manager

    # ---- 入口 ----
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
        # 请求准入观测（R3/B11）：运行器入口≈请求处理起点，首个发布事件时
        # 登记到达→首业务事件时长；arun/astream 共用本入口，两路径均覆盖
        recorder.admission_started_wall = time.time()

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
                        await task  # 业务任务已进入终态，正常收尾
                        return
                else:
                    # 业务任务先结束（异常路径）：排空队列后传播异常
                    get_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await get_task
                    with contextlib.suppress(asyncio.QueueEmpty):
                        while True:
                            yield queue.get_nowait()
                    await task
                    return
        except asyncio.CancelledError:
            # 消费端断连/被取消：停止后续动作，业务任务负责记录取消终态
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

    # ---- 业务循环（在独立任务中执行）----
    async def _business(
        self,
        ctx: RunContext,
        budget: Budget,
        recorder: RunRecorder,
        run_input: RunInput,
        started_epoch_ms: int,
    ) -> RunResult:
        system = self._system_prompt()
        history: list[Message] = [
            Message(role="system", content=system),
            Message(role="user", content=run_input.query),
        ]
        recorder.emit_thinking("正在理解您的问题并规划工具调用...")

        response = ""
        intent = ""
        status = "failed"
        stop_reason = ""
        error: Optional[str] = None
        exit_action = ""

        try:
            while True:
                budget.begin_iteration()
                decision, decided_at = await self._decide(history, ctx)
                recorder.record_decision(decision, start_wall=decided_at)
                intent = cs_policy.detect_intent_hint(run_input.query)

                if decision.action != "call_tools":
                    exit_action = decision.action
                    self._append_control_history(history, decision)
                    break

                if not decision.tool_calls:
                    # 原生协议下不会出现（无调用即 finalize）；JSON 协议空调用
                    # 属于非法决策，不按“无工具可调用”结束
                    raise InvalidDecisionError("call_tools 决策未包含任何调用")

                history.append(Message(
                    role="assistant", content="", tool_calls=decision.tool_calls
                ))
                for call in decision.tool_calls:
                    obs = await self.executor.run(call, ctx, budget, recorder)
                    recorder.record_observation(obs, args=call.arguments)
                    history.append(Message(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        content=json.dumps(
                            obs.data if not obs.error else {"error": obs.error},
                            ensure_ascii=False,
                        ),
                    ))

            # ---- 最终答复阶段（禁用工具）----
            status, stop_reason, response, intent = await self._finalize(
                exit_action, ctx, recorder, system, run_input.query
            )
        except BudgetExceeded as exc:
            status, stop_reason = "budget_exhausted", exc.reason
        except DeadlineExceeded:
            status, stop_reason = "timed_out", STOP_REQUEST_DEADLINE
        except _ToolFailure as exc:
            status, stop_reason, error = "failed", STOP_TOOL_FAILED, str(exc)
        except _EmptyAnswer:
            status, stop_reason, error = "failed", STOP_EMPTY_ANSWER, "答复为空"
        except InvalidDecisionError as exc:
            status, stop_reason, error = "failed", STOP_INVALID_DECISION, str(exc)
        except ProviderError as exc:
            status, stop_reason, error = "failed", STOP_PROVIDER_ERROR, str(exc)
        except asyncio.CancelledError:
            # 客户端断连/调用方取消：停止后续动作，保留终态证据；
            # 迟到工具结果不会覆盖本状态（观察只经运行器登记）
            result = self._build_result(
                ctx, budget, recorder, "cancelled", STOP_CLIENT_DISCONNECT,
                response or recorder.answer_text, intent, error, started_epoch_ms,
            )
            logger.warning(
                "运行取消 %s", json.dumps(result.model_dump(), ensure_ascii=False)
            )
            raise
        except Exception as exc:  # noqa: BLE001  未知异常按失败终态收敛
            status, stop_reason, error = "failed", "internal_error", str(exc)
            logger.exception("运行内部异常 run_id=%s", ctx.run_id)

        # 失败路径保留已输出的部分答复，但不改变终态（不得据此判定 completed）
        response = response or recorder.answer_text
        result = self._build_result(
            ctx, budget, recorder, status, stop_reason, response, intent,
            error, started_epoch_ms,
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
                "total_tokens": result.metrics.get("total_tokens"),
                "evidence_kind": result.metrics.get("evidence_kind"),
            }, ensure_ascii=False)
        )
        return result

    # ---- 决策与执行 ----
    def _observe_context(self, messages: list[Message]) -> None:
        """模型调用前观测上下文窗口占用（Mock tokenization 口径，课程第 07 讲指标）。"""
        prompt_tokens = token_count("".join(m.content or "" for m in messages))
        observe_context_occupancy(
            prompt_tokens, int(getattr(self.settings, "context_window_tokens", 0))
        )

    async def _decide(self, history: list[Message], ctx: RunContext):
        """一次模型决策：受整体 deadline 约束，取消时关闭模型连接。

        返回 (决策, 调用开始墙钟)：开始墙钟交给观测层作为 generation 的
        真实起点（R3/P5，消除事件发射时刻起算的区间后移）。
        """
        self._observe_context(history)
        tools = self.executor.enabled_schemas()
        decided_at = time.time()
        try:
            return await asyncio.wait_for(
                self.provider.decide(history, tools, ctx),
                timeout=ctx.remaining_s(),
            ), decided_at
        except asyncio.TimeoutError as exc:
            raise DeadlineExceeded() from exc

    async def _finalize(
        self,
        exit_action: str,
        ctx: RunContext,
        recorder: RunRecorder,
        system: str,
        query: str,
    ) -> tuple[str, str, str, str]:
        """客服策略出口：澄清 / 人工处理建议 / 答复生成（含无结果致歉）。

        未解决的执行错误不能转换成“查询无结果”或澄清答复；
        finalize 但仅有被拒调用时同样按工具失败处理。
        """
        unresolved = recorder.unresolved_tool_errors()
        if unresolved:
            raise _ToolFailure(f"工具执行错误未解决: {', '.join(unresolved)}")

        if exit_action == "clarify":
            text = cs_policy.CLARIFY_TEXT
            recorder.mark_evidence("template")
            recorder.emit_answer(text, source="template")
            return "completed", STOP_CLARIFICATION, text, "clarification"

        if exit_action == "handoff":
            text = cs_policy.HANDOFF_TEXT
            recorder.mark_evidence("template")
            recorder.emit_answer(text, source="template")
            return "completed", STOP_HANDOFF, text, "complaint"

        # finalize：依据工具实际观察决定生成或直接定稿
        agg = cs_policy.aggregate_observations(recorder.observations)
        if cs_policy.has_found(agg):
            response = await self._generate(ctx, recorder, system, query, agg)
            if not response.strip():
                raise _EmptyAnswer()
            return (
                "completed", STOP_ANSWERED, response,
                cs_policy.detect_intent_hint(query),
            )
        if recorder.any_executed:
            # 查询过但无结果：如实致歉
            text = cs_policy.NOT_FOUND_TEXT
            recorder.mark_evidence("template")
            recorder.emit_answer(text, source="template")
            return (
                "completed", STOP_NOT_FOUND, text,
                cs_policy.detect_intent_hint(query),
            )
        if recorder.rejections:
            # finalize 但没有任何实际执行（仅有被拒调用）：不得伪装查询无结果
            reasons = "; ".join(r["reason"] for r in recorder.rejections[:3])
            raise _ToolFailure(f"调用被拒绝且未恢复: {reasons}")
        # 未执行任何工具即定稿：按澄清出口（与 v1 行为一致）
        text = cs_policy.CLARIFY_TEXT
        recorder.mark_evidence("template")
        recorder.emit_answer(text, source="template")
        return "completed", STOP_CLARIFICATION, text, "clarification"

    async def _generate(
        self,
        ctx: RunContext,
        recorder: RunRecorder,
        system: str,
        query: str,
        agg: dict,
    ) -> str:
        """最终答复生成：增量产出 answer 事件；此阶段禁用工具。"""
        recorder.mark_evidence(
            getattr(self.provider, "evidence_kind", "non_stream")
        )
        recorder.emit_thinking("正在整合查询结果并生成答复...")
        messages = cs_policy.build_generation_messages(
            system, query, agg, self._response_gen_text()
        )
        self._observe_context(messages)
        parts: list[str] = []
        remaining = ctx.remaining_s()
        if remaining <= 0:
            raise DeadlineExceeded()
        try:
            async with asyncio.timeout(remaining):
                async for chunk in self.provider.stream_answer(messages, ctx):
                    if chunk.content:
                        recorder.emit_answer(chunk.content, source="model")
                        parts.append(chunk.content)
                    if chunk.usage is not None:
                        # R3/P5：final_answer 携带答案段真实时长与首片段起点
                        recorder.record_answer_usage(
                            chunk.usage,
                            latency_ms=recorder.answer_span_ms(),
                            start_wall=recorder.answer_start_wall,
                        )
        except TimeoutError as exc:
            # 已输出的部分答复保留在响应中，但终态为超时，不判合格
            raise DeadlineExceeded() from exc
        return "".join(parts)

    # ---- 组装辅助 ----
    def _append_control_history(self, history: list[Message], decision) -> None:
        """控制调用需要配对观察消息后，再进入下一阶段（无业务副作用）。"""
        if decision.control_calls:
            history.append(Message(
                role="assistant", content=decision.content,
                tool_calls=decision.control_calls,
            ))
            for call in decision.control_calls:
                history.append(Message(
                    role="tool", tool_call_id=call.id, name=call.name,
                    content=json.dumps(
                        {"ok": True, "action": decision.action},
                        ensure_ascii=False,
                    ),
                ))
        elif decision.content:
            history.append(Message(role="assistant", content=decision.content))

    def _system_prompt(self) -> str:
        if self.prompt_manager is None:
            return ""
        return self.prompt_manager.system_prompt(self.executor.describe())

    def _response_gen_text(self) -> str:
        if self.prompt_manager is None:
            return ""
        return self.prompt_manager.raw("response_gen.txt")

    def _build_result(
        self,
        ctx: RunContext,
        budget: Budget,
        recorder: RunRecorder,
        status: str,
        stop_reason: str,
        response: str,
        intent: str,
        error: Optional[str],
        started_epoch_ms: int,
    ) -> RunResult:
        counters = budget.counters()
        recorder.intent = intent
        metrics = recorder.metrics(counters, recorder.elapsed_ms())
        # 安全状态逐笔采集（课程第 19 讲安全门禁口径）：对最终答复做敏感信息/
        # 越界内容检查，结果随运行结果透出，供压测侧聚合为门禁的违规计数。
        # 与阶段三在置信检查节点调用的是同一共享实现（harness/guardrail.py）。
        metrics["guardrail"] = guardrail.check(response)
        return RunResult(
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
            trace=recorder.trace(counters),
        )
