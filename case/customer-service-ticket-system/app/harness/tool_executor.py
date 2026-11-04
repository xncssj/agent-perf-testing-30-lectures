"""Harness 工具执行器：Schema 校验、预算、超时、有限重试与迟到结果丢弃。

职责边界（共享 Harness 设计说明 第 3.2、5.2 节）：
- 注册表只做定义与查找；本执行器统一负责真正的等待超时与有限重试。
- 同一轮工具按顺序执行，不做并行扇出；单进程内有界工作线程池
  （tool_workers 并发线程 + tool_queue_size 等待槽），满时明确返回过载失败。
- 重试仅用于超时（TimeoutError）；退避 100ms/200ms 翻倍封顶 1s，
  且退避与再尝试都受整体 deadline 与工具尝试预算约束。
- 参数校验拒绝的调用返回关联错误观察，但不产生实际执行的 tool_call 事件，
  也不计入实际执行尝试；拒绝原因记录在 trace 中。
- 工作槽在线程体自然结束时才释放（等待超时不提前释放）；迟到结果被丢弃，
  不更新已终止的运行状态。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import time
from typing import Any, Protocol

from .registry import ToolRegistry
from .budget import Budget
from .contracts import RunContext, ToolCall, ToolObservation
from .metrics import TOOL_CALLS_INFLIGHT, TOOL_OVERLOAD_TOTAL, TOOL_SLOTS_TOTAL
from .trace_context import reset_dependency_context, set_dependency_context

# 重试退避基数（秒）：首次 100ms、第二次 200ms，之后翻倍并封顶 1s
BACKOFF_BASE_S = 0.1
BACKOFF_CAP_S = 1.0


class RunRecorderProtocol(Protocol):
    """执行器对事件记录器的最小依赖（实现见 observation.RunRecorder）。

    parent_span_id 为调用方真实父步骤（006 R02）；error_kind 为错误类别，
    事件不携带异常原文（006 R06）。
    """

    def emit_tool_call(self, tool: str, tool_call_id: str, attempt: int,
                       args: dict, parent_span_id: str = "") -> None: ...

    def emit_tool_result(self, tool: str, tool_call_id: str, attempt: int,
                         data: dict, error: str | None,
                         duration_ms: float, parent_span_id: str = "",
                         error_kind: str | None = None) -> None: ...

    def record_rejection(self, name: str, tool_call_id: str,
                         reason: str) -> None: ...


class ToolExecutor:
    """有界同步只读工具执行器（跨运行共享，仅在事件循环内调用）。"""

    def __init__(
        self,
        registry: ToolRegistry,
        tool_workers: int = 4,
        tool_queue_size: int = 16,
        sync_mode: bool = False,
    ) -> None:
        self._registry = registry
        self._workers = max(1, int(tool_workers))
        self._queue_size = max(0, int(tool_queue_size))
        # 工作槽 = 并发线程 + 等待槽；事件循环侧占用，线程体结束时线程安全释放
        self._slots = asyncio.Semaphore(self._workers + self._queue_size)
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.in_flight = 0  # 当前占用的工作槽数（诊断用）
        # 槽位容量导出为指标：看板以「在途逼近槽位上限 + 过载拒绝」呈现队列压力
        TOOL_SLOTS_TOTAL.set(self._workers + self._queue_size)
        # 同步模式（阶段三）：槽位改用线程信号量，整个 workflow 在
        # to_thread 工作线程内执行，无事件循环可调度
        self.sync_mode = sync_mode
        if sync_mode:
            import threading

            self._thread_slots = threading.BoundedSemaphore(
                self._workers + self._queue_size
            )
            self._flight_lock = threading.Lock()

    # ---- 对外入口 ----
    async def run(
        self,
        call: ToolCall,
        ctx: RunContext,
        budget: Budget,
        recorder: RunRecorderProtocol,
        parent_span_id: str = "",
    ) -> ToolObservation:
        """执行一次工具调用，返回全部尝试结束后的最终观察。

        仅超时会按工具 retry 配置自动重试；未知工具、禁用工具与参数校验
        失败立即返回 rejected 观察，不产生事件、不消耗执行预算。
        parent_span_id 为调用方的真实父步骤（如子 Agent span），随事件
        导出为父子关系（006 R02），并作为依赖上下文的步骤身份（006 R03）。
        """
        entry = self._registry.get_entry(call.name)
        if entry is None:
            reason = f"未知工具: {call.name}"
            recorder.record_rejection(call.name, call.id, reason)
            return ToolObservation(
                tool_call_id=call.id, name=call.name, rejected=True,
                error=reason, error_kind="unknown_tool",
            )
        if not entry.enabled:
            reason = f"工具已禁用: {call.name}"
            recorder.record_rejection(call.name, call.id, reason)
            return ToolObservation(
                tool_call_id=call.id, name=call.name, rejected=True,
                error=reason, error_kind="disabled_tool",
            )

        args_model = getattr(entry.tool, "args_model", None)
        if args_model is not None:
            try:
                validated = args_model(**(call.arguments or {}))
            except Exception as exc:  # noqa: BLE001  pydantic 校验错误
                reason = f"参数校验失败: {exc}"
                recorder.record_rejection(call.name, call.id, reason)
                return ToolObservation(
                    tool_call_id=call.id, name=call.name, rejected=True,
                    error=reason, error_kind="invalid_arguments",
                )
            args = validated.model_dump()
        else:
            args = dict(call.arguments or {})

        max_attempts = 1 + max(0, int(entry.tool.retry))
        # 调用内尝试序号（1=首次，超时自动重试时递增）：事件与观察都用它，
        # 这样"第 2 次尝试"就代表一次重试，可直接支撑"重试增加"信号；
        # 全局尝试计数（含所有调用的重试）由预算承担，见 metrics.tool_attempts
        call_attempt = 0
        while True:
            # 每次实际尝试（含自动重试）前重新检查预算与整体期限
            budget.consume_tool_attempt()
            call_attempt += 1
            recorder.emit_tool_call(call.name, call.id, call_attempt, args,
                                    parent_span_id=parent_span_id)
            token = set_dependency_context(ctx.run_id, call.id)
            try:
                obs = await self._attempt(entry.tool, call.id, args, ctx, call_attempt)
            finally:
                reset_dependency_context(token)
            recorder.emit_tool_result(
                call.name, call.id, call_attempt, obs.data, obs.error,
                obs.duration_ms, parent_span_id=parent_span_id,
                error_kind=obs.error_kind,
            )
            if not obs.timed_out or call_attempt >= max_attempts:
                return obs
            # 仅超时自动重试：按退避等待，等待仍受整体 deadline 约束
            backoff = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (call_attempt - 1)))
            backoff = min(backoff, ctx.remaining_s())
            if backoff > 0:
                await asyncio.sleep(backoff)

    # ---- 内部实现 ----
    async def _attempt(self, tool, call_id: str, args: dict,
                       ctx: RunContext, attempt: int) -> ToolObservation:
        """执行单次工具尝试：有界等待；超时后线程继续运行、槽位不提前释放。"""
        loop = asyncio.get_running_loop()
        self._ensure_pool(loop)
        timeout_s = min(float(tool.timeout_ms) / 1000.0, ctx.remaining_s())
        t0 = time.perf_counter()
        if self._slots.locked():
            # 等待槽已满：明确过载失败，不无限排队
            TOOL_OVERLOAD_TOTAL.inc()
            return ToolObservation(
                tool_call_id=call_id, name=tool.name, attempt=attempt,
                error="工具执行池过载", error_kind="overloaded",
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        await self._slots.acquire()
        self.in_flight += 1
        TOOL_CALLS_INFLIGHT.inc()
        try:
            # ThreadPoolExecutor 不自动传播 contextvars：在提交线程快照
            # 当前上下文（含依赖调用上下文），在工作线程内原样执行（006 R03）
            snapshot = contextvars.copy_context()
            future = loop.run_in_executor(
                self._pool, snapshot.run, self._thread_body, loop, tool, args
            )
        except BaseException:
            # 尚未提交成功：线程体不会运行，槽位在此归还
            self._slot_finished()
            raise
        # asyncio.wait 超时不取消线程体：槽位由线程体结束时释放，
        # 迟到结果无消费者；运行被取消时线程体同样自然收尾
        done, pending = await asyncio.wait({future}, timeout=timeout_s)
        if pending:
            return ToolObservation(
                tool_call_id=call_id, name=tool.name, attempt=attempt,
                error=f"{tool.name} 等待超时（{timeout_s * 1000.0:.0f}ms）",
                error_kind="timeout", timed_out=True,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        ok, data, error, tool_timed_out = future.result()
        return ToolObservation(
            tool_call_id=call_id, name=tool.name, attempt=attempt,
            data=data if ok else {},
            error=error, error_kind=("exception" if error else None),
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            timed_out=tool_timed_out,
        )

    def _ensure_pool(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix="tool-worker"
            )
        self._loop = loop

    # ---- 同步入口（阶段三 sync 节点在 to_thread 工作线程中使用）----
    def run_sync(self, call: ToolCall, ctx: RunContext, budget: Budget,
                 recorder: RunRecorderProtocol,
                 parent_span_id: str = "") -> ToolObservation:
        """阻塞版 run()：预算/校验/超时/重试语义一致，供同步运行流程使用。"""
        entry = self._registry.get_entry(call.name)
        if entry is None:
            reason = f"未知工具: {call.name}"
            recorder.record_rejection(call.name, call.id, reason)
            return ToolObservation(
                tool_call_id=call.id, name=call.name, rejected=True,
                error=reason, error_kind="unknown_tool",
            )
        if not entry.enabled:
            reason = f"工具已禁用: {call.name}"
            recorder.record_rejection(call.name, call.id, reason)
            return ToolObservation(
                tool_call_id=call.id, name=call.name, rejected=True,
                error=reason, error_kind="disabled_tool",
            )
        args_model = getattr(entry.tool, "args_model", None)
        if args_model is not None:
            try:
                validated = args_model(**(call.arguments or {}))
            except Exception as exc:  # noqa: BLE001
                reason = f"参数校验失败: {exc}"
                recorder.record_rejection(call.name, call.id, reason)
                return ToolObservation(
                    tool_call_id=call.id, name=call.name, rejected=True,
                    error=reason, error_kind="invalid_arguments",
                )
            args = validated.model_dump()
        else:
            args = dict(call.arguments or {})

        max_attempts = 1 + max(0, int(entry.tool.retry))
        # 与异步入口同口径：调用内尝试序号（1=首次），全局计数由预算承担
        call_attempt = 0
        while True:
            budget.allocate_tool_attempt()
            call_attempt += 1
            recorder.emit_tool_call(call.name, call.id, call_attempt, args,
                                    parent_span_id=parent_span_id)
            token = set_dependency_context(ctx.run_id, call.id)
            try:
                obs = self._attempt_sync(entry.tool, call.id, args, ctx, call_attempt)
            finally:
                reset_dependency_context(token)
            recorder.emit_tool_result(
                call.name, call.id, call_attempt, obs.data, obs.error,
                obs.duration_ms, parent_span_id=parent_span_id,
                error_kind=obs.error_kind,
            )
            if not obs.timed_out or call_attempt >= max_attempts:
                return obs
            backoff = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (call_attempt - 1)))
            backoff = min(backoff, ctx.remaining_s())
            if backoff > 0:
                time.sleep(backoff)

    def _attempt_sync(self, tool, call_id: str, args: dict,
                      ctx: RunContext, attempt: int) -> ToolObservation:
        """同步单次尝试：线程信号量限槽；超时后线程体自然收尾并释放槽位。"""
        self._ensure_pool(None)
        timeout_s = min(float(tool.timeout_ms) / 1000.0, ctx.remaining_s())
        t0 = time.perf_counter()
        if not self._thread_slots.acquire(blocking=False):
            TOOL_OVERLOAD_TOTAL.inc()
            return ToolObservation(
                tool_call_id=call_id, name=tool.name, attempt=attempt,
                error="工具执行池过载", error_kind="overloaded",
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        with self._flight_lock:
            self.in_flight += 1
        TOOL_CALLS_INFLIGHT.inc()
        try:
            # 同步路径同样以提交线程的上下文快照进入工作线程（006 R03）
            snapshot = contextvars.copy_context()
            future = self._pool.submit(
                snapshot.run, self._thread_body_sync, tool, args
            )
        except BaseException:
            self._sync_slot_finished()
            raise
        try:
            outcome = future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            return ToolObservation(
                tool_call_id=call_id, name=tool.name, attempt=attempt,
                error=f"{tool.name} 等待超时（{timeout_s * 1000.0:.0f}ms）",
                error_kind="timeout", timed_out=True,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )
        ok, data, error, tool_timed_out = outcome
        return ToolObservation(
            tool_call_id=call_id, name=tool.name, attempt=attempt,
            data=data if ok else {},
            error=error, error_kind=("exception" if error else None),
            duration_ms=(time.perf_counter() - t0) * 1000.0,
            timed_out=tool_timed_out,
        )

    def _sync_slot_finished(self) -> None:
        """线程安全归还工作槽（线程体结束时直接调用）。"""
        with self._flight_lock:
            self.in_flight = max(0, self.in_flight - 1)
        TOOL_CALLS_INFLIGHT.dec()
        self._thread_slots.release()

    def _thread_body_sync(self, tool, args: dict):
        """同步模式线程体：结束时才释放工作槽；迟到结果无消费者。"""
        try:
            out = tool.execute(**args)
            if out.error:
                return (False, {}, out.error, out.timed_out)
            return (True, out.result or {}, None, False)
        except Exception as exc:  # noqa: BLE001
            return (False, {}, str(exc), False)
        finally:
            self._sync_slot_finished()

    # ---- 注册表代理（Provider 与系统提示词使用）----
    def describe(self) -> str:
        """仅描述启用的工具（进入系统提示词）。"""
        return self._registry.describe()

    def enabled_schemas(self) -> dict[str, dict]:
        """启用工具的 JSON Schema（提交给原生工具协议）。"""
        from .params import TOOL_SCHEMAS

        return {
            name: TOOL_SCHEMAS[name]
            for name in self._registry.enabled_names()
            if name in TOOL_SCHEMAS
        }

    def _slot_finished(self) -> None:
        """事件循环侧归还工作槽（由线程体通过 call_soon_threadsafe 调用）。"""
        self.in_flight = max(0, self.in_flight - 1)
        TOOL_CALLS_INFLIGHT.dec()
        self._slots.release()

    def _thread_body(self, loop, tool, args: dict):
        """线程体内执行工具；函数返回时才释放工作槽，迟到结果无消费者。"""

        def release() -> None:
            try:
                loop.call_soon_threadsafe(self._slot_finished)
            except RuntimeError:
                pass  # 事件循环已关闭：进程退出场景，槽位随之销毁

        try:
            out = tool.execute(**args)
            if out.error:
                return (False, {}, out.error, out.timed_out)
            return (True, out.result or {}, None, False)
        except Exception as exc:  # noqa: BLE001
            return (False, {}, str(exc), False)
        finally:
            release()

    async def aclose(self) -> None:
        """关闭工作池：不等待仍在执行的阻塞工具（取消清理语义）。"""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
