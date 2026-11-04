"""Harness 过程观测：事件编号、耗时与用量汇总。

只负责记录与汇总，不承担业务合格判定（判定属评测采集，见
tests/performance/common.py）。指标口径见 共享 Harness 设计说明 第 7.1 节：
- ttft_ms / tpot_ms 仅在有可靠 token 级时间证据时填写，本期统一为 None；
  一般模型 SSE 提供的是文本片段，不能据片段数还原逐 token 到达时间。
- first_answer_ms 与 chunk_intervals_ms 为最终答复片段的实测值，本期必须可测。
- usage_source：reported / estimated / missing；整笔混合时为 mixed。
- evidence_kind：provider_stream / mock / replay / template / non_stream。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from .contracts import (
    SCHEMA_VERSION,
    ModelUsage,
    RunContext,
    ToolObservation,
)
from .metrics import observe_request_admission


class RunRecorder:
    """一次运行的事件与指标记录器（仅运行器协程内使用）。"""

    def __init__(
        self,
        ctx: RunContext,
        queue: Optional[asyncio.Queue] = None,
        internal_sink: Any = None,
    ) -> None:
        self.ctx = ctx
        self._queue = queue
        self._internal_sink = internal_sink
        # 记录构造时的事件循环：阶段三整个 workflow 在 to_thread 工作线程内
        # 执行，事件需跨线程安全发布（call_soon_threadsafe）
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._seq = 0
        self._t0 = time.perf_counter()
        # 请求准入（R3/B11）：请求到达时刻，由运行器入口设置；首个事件发布时
        # 观测 admission 时长并清空（每运行只记一次）
        self.admission_started_wall: Optional[float] = None
        # 用量明细：[(kind, ModelUsage)]
        self._usage: list[tuple[str, ModelUsage]] = []
        # 答复片段计时
        self.answer_chunks = 0
        self.answer_chars = 0
        self.answer_text = ""  # 已输出的答复全文（含模板出口与失败前的部分答复）
        self.first_answer_ms: Optional[float] = None
        self.chunk_intervals_ms: list[float] = []
        self._t_prev_chunk: Optional[float] = None
        # 答案段真实墙钟（R3/P5）：首个 answer 片段时刻，final_answer 的
        # generation 起点用它而非事件发射时刻（消除区间后移）
        self._t_first_answer_wall: Optional[float] = None
        # 工具观察与拒绝
        self._tool_last_error: dict[str, bool] = {}
        self._observations: list[dict] = []
        self._rejections: list[dict] = []
        # 决策与证据
        self.decision_rounds = 0
        self._evidence: set[str] = set()
        self._events: list[dict] = []  # 非 answer 事件的紧凑记录（进入 trace）
        self.intent = ""

    # ---- 基础 ----
    def _next(self, type_: str) -> dict:
        self._seq += 1
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": self.ctx.task_id,
            "run_id": self.ctx.run_id,
            "batch_id": self.ctx.batch_id,
            "seq": self._seq,
            "ts_ms": round((time.perf_counter() - self._t0) * 1000.0, 3),
            "ts_wall": time.time(),
            "type": type_,
        }

    def _publish(self, event: dict) -> None:
        # 首个发布事件即登记请求准入时长（R3/B11：请求到达→首业务事件）
        if self.admission_started_wall is not None:
            observe_request_admission(time.time() - self.admission_started_wall)
            self.admission_started_wall = None
        if self._queue is None:
            return
        if self._loop is not None and self._loop.is_running():
            # 跨线程（to_thread 工作线程）安全投递；循环内调用同样适用
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
                return
            except RuntimeError:
                pass  # 循环已关闭：进程收尾场景，事件随之丢弃
        self._queue.put_nowait(event)

    # ---- 公共事件 ----
    def emit_thinking(self, content: str) -> None:
        ev = self._next("thinking")
        ev["content"] = content
        self._events.append({"type": "thinking", "ts_ms": ev["ts_ms"], "content": content})
        self._publish(ev)

    def emit_tool_call(self, tool: str, tool_call_id: str, attempt: int,
                       args: dict, parent_span_id: str = "") -> None:
        ev = self._next("tool_call")
        ev.update({"tool": tool, "tool_call_id": tool_call_id,
                   "attempt": attempt, "args": args,
                   "parent_span_id": parent_span_id})
        self._events.append({
            "type": "tool_call", "ts_ms": ev["ts_ms"], "tool": tool,
            "tool_call_id": tool_call_id, "attempt": attempt,
        })
        self._publish(ev)

    def emit_tool_result(self, tool: str, tool_call_id: str, attempt: int,
                         data: dict, error: str | None, duration_ms: float,
                         parent_span_id: str = "",
                         error_kind: str | None = None) -> None:
        """error_kind 为错误类别（timeout/exception/overloaded 等）；
        结构化事件不携带异常原文（006 R06 内容最小化）。"""
        ev = self._next("tool_result")
        ev.update({
            "tool": tool, "tool_call_id": tool_call_id, "attempt": attempt,
            "data": data, "error": error, "duration_ms": round(duration_ms, 3),
            "parent_span_id": parent_span_id, "error_kind": error_kind,
        })
        self._events.append({
            "type": "tool_result", "ts_ms": ev["ts_ms"], "tool": tool,
            "tool_call_id": tool_call_id, "attempt": attempt,
            "error_kind": error_kind, "duration_ms": round(duration_ms, 3),
            "found": bool(data.get("found")) if data else False,
        })
        self._publish(ev)

    def record_answer_only(self, content: str, source: str) -> None:
        """仅登记答复计账（片段计时/计数），不经队列发布——供生成器自行
        构造事件直接产出的场景（阶段三答复流）使用。"""
        if not content:
            return
        now = time.perf_counter()
        self.answer_chunks += 1
        self.answer_chars += len(content)
        self.answer_text += content
        if self.first_answer_ms is None:
            self.first_answer_ms = (now - self._t0) * 1000.0
        elif self._t_prev_chunk is not None:
            self.chunk_intervals_ms.append(round((now - self._t_prev_chunk) * 1000.0, 3))
        self._t_prev_chunk = now

    def build_public_event(self, type_: str, **payload) -> dict:
        """分配 seq/ts 构造公共事件但不入队（由调用方直接产出）。"""
        ev = self._next(type_)
        ev.update(payload)
        return ev

    def emit_answer(self, content: str, source: str) -> None:
        if not content:
            return
        now = time.perf_counter()
        self.answer_chunks += 1
        self.answer_chars += len(content)
        self.answer_text += content
        if self.first_answer_ms is None:
            self.first_answer_ms = (now - self._t0) * 1000.0
            self._t_first_answer_wall = time.time()
        elif self._t_prev_chunk is not None:
            self.chunk_intervals_ms.append(round((now - self._t_prev_chunk) * 1000.0, 3))
        self._t_prev_chunk = now
        ev = self._next("answer")
        ev.update({"content": content, "source": source})
        self._publish(ev)

    def emit_done(self, payload: dict) -> None:
        ev = self._next("done")
        ev.update(payload)
        self._publish(ev)

    def emit_agent_invoke(self, agent: str, status: str, span_id: str,
                          attempts: int, start_wall: float | None = None,
                          end_wall: float | None = None,
                          parent_span_id: str = "",
                          error_kind: str | None = None) -> None:
        """阶段二：一次子 Agent 调用（含重试/熔断）的调度结果事件。

        start_wall/end_wall 为本次调用的真实起止墙钟（006 R02：不能用
        同一时间戳伪装耗时）；缺省回退为事件墙钟，仅旧调用方未传时出现。
        """
        ev = self._next("agent_invoke")
        ev.update({"agent": agent, "status": status, "span_id": span_id,
                   "attempts": attempts, "parent_span_id": parent_span_id,
                   "error_kind": error_kind})
        if start_wall is not None:
            ev["start_wall"] = start_wall
        if end_wall is not None:
            ev["end_wall"] = end_wall
        self._events.append({
            "type": "agent_invoke", "ts_ms": ev["ts_ms"], "agent": agent,
            "status": status, "span_id": span_id, "attempts": attempts,
        })
        self._publish(ev)

    def emit_node(self, node_id: str, node_type: str, latency_ms: float,
                  status: str, attempts: int = 1) -> None:
        """阶段三：节点执行边界事件（携带 node_id，共享 Harness 设计说明 第 8.2 节）。"""
        ev = self._next("node")
        ev.update({"node_id": node_id, "node_type": node_type,
                   "latency_ms": round(latency_ms, 3), "status": status,
                   "attempts": attempts})
        self._events.append({
            "type": "node", "ts_ms": ev["ts_ms"], "node_id": node_id,
            "node_type": node_type, "status": status, "attempts": attempts,
        })
        self._publish(ev)

    def emit_validation(self, validation: dict) -> None:
        """阶段二：聚合校验 Agent 的结论事件。"""
        ev = self._next("validation")
        ev.update({"validation": validation})
        self._events.append({
            "type": "validation", "ts_ms": ev["ts_ms"], "validation": validation,
        })
        self._publish(ev)

    # ---- 内部观测（不进入 SSE）----
    def record_decision(self, decision, kind: str = "decision",
                        start_wall: float | None = None) -> None:
        """记录一次模型决策的用量与内部事件。

        start_wall 为本次模型调用的真实开始墙钟（R3/P5：携带后 exporter 的
        generation 区间按真实起止换算，消除「发射时刻起算、整体后移一个
        latency」的错位）；缺省回退事件发射时刻（旧调用方兼容）。
        """
        self.decision_rounds += 1
        self._usage.append((kind, decision.usage))
        self._emit_model_call(kind, decision.model, decision.latency_ms,
                              decision.usage, start_wall=start_wall)

    def record_model_usage(self, kind: str, model: str, latency_ms: float,
                           usage: ModelUsage, parent_span_id: str = "",
                           start_wall: float | None = None) -> None:
        """登记一次模型调用的用量（多 Agent 的规划/决策/校验/答复共用）。

        kind 区分调用类别（进入 trace 的 usage_detail 语义）；不消耗决策轮次，
        轮次由各运行器按自身编排语义登记。parent_span_id 为该次模型调用
        所属的真实父步骤（如子 Agent span），空表示 Trace 根（006 R02）。
        start_wall 语义同 record_decision（R3/P5）。
        """
        self._usage.append((kind, usage))
        self.model_calls = getattr(self, "model_calls", 0) + 1
        self._emit_model_call(kind, model, latency_ms, usage,
                              parent_span_id=parent_span_id, start_wall=start_wall)

    def record_answer_usage(self, usage: ModelUsage,
                            latency_ms: float = 0.0,
                            start_wall: float | None = None) -> None:
        """登记最终答复段的用量（R3/P5：可携带答案段真实时长与起点）。

        latency_ms 为答案生成段的真实耗时（首个 answer 片段→末片段），
        start_wall 为首片段墙钟；缺省保持旧口径（零时长、发射时刻起算）。
        """
        if usage is not None:
            self._usage.append(("final_answer", usage))
            self._emit_model_call("final_answer", "", latency_ms, usage,
                                  start_wall=start_wall)

    @property
    def answer_start_wall(self) -> float | None:
        """首个答案片段的墙钟（final_answer generation 的真实起点）。"""
        return self._t_first_answer_wall

    def answer_span_ms(self) -> float:
        """答案生成段真实耗时（首片段→当前时刻；无片段时为 0）。"""
        if self._t_first_answer_wall is None:
            return 0.0
        return max(0.0, (time.time() - self._t_first_answer_wall) * 1000.0)

    def _emit_model_call(self, kind: str, model: str, latency_ms: float,
                         usage: ModelUsage, parent_span_id: str = "",
                         start_wall: float | None = None) -> None:
        if self._internal_sink is None:
            return
        ev = self._next("model_call")
        ev.update({
            "call_kind": kind, "model": model,
            "latency_ms": round(latency_ms, 3),
            "usage": usage.model_dump(),
            "parent_span_id": parent_span_id,
        })
        if start_wall is not None:
            ev["start_wall"] = start_wall
        try:
            self._internal_sink(ev)
        except Exception:  # noqa: BLE001  观测失败不影响业务
            pass

    # ---- 工具观察与拒绝 ----
    def record_observation(self, obs: ToolObservation, args: dict | None = None) -> None:
        """登记最终观察：跟踪每个工具最后一次尝试是否出错。"""
        self._tool_last_error[obs.name] = bool(obs.error) and not obs.rejected
        self._observations.append({
            "tool": obs.name,
            "tool_call_id": obs.tool_call_id,
            "attempt": obs.attempt,
            "args": dict(args or {}),
            "data": dict(obs.data or {}),
            "duration_ms": round(obs.duration_ms, 3),
            "error": obs.error,
            "error_kind": obs.error_kind,
            "rejected": obs.rejected,
            "found": bool((obs.data or {}).get("found")),
        })

    def record_rejection(self, name: str, tool_call_id: str, reason: str) -> None:
        self._rejections.append(
            {"tool": name, "tool_call_id": tool_call_id, "reason": reason}
        )

    def unresolved_tool_errors(self) -> list[str]:
        """存在执行错误且此后未成功恢复的工具名列表。"""
        return [t for t, err in self._tool_last_error.items() if err]

    @property
    def observations(self) -> list[dict]:
        return list(self._observations)

    @property
    def rejections(self) -> list[dict]:
        return list(self._rejections)

    @property
    def any_executed(self) -> bool:
        return bool(self._tool_last_error)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    # ---- 证据 ----
    def mark_evidence(self, kind: str) -> None:
        self._evidence.add(kind)

    # ---- 汇总 ----
    def usage_summary(self) -> tuple[dict, dict, str]:
        """返回 (全流程 usage, 最终答复 usage, usage_source)。"""
        def merge(kinds: tuple[str, ...]) -> dict:
            picked = [u for k, u in self._usage if k in kinds]
            if not picked:
                return {"prompt_tokens": None, "completion_tokens": None,
                        "total_tokens": None}
            return {
                "prompt_tokens": _sum_or_none(u.prompt_tokens for u in picked),
                "completion_tokens": _sum_or_none(u.completion_tokens for u in picked),
                "total_tokens": _sum_or_none(u.total_tokens for u in picked),
            }

        # 全流程用量累计所有类别的模型调用（单 Agent 决策、多 Agent 的
        # 规划/子 Agent 决策/校验/答复均计入）；最终答复单独统计
        all_kinds = tuple({k for k, _ in self._usage} | {"decision", "final_answer"})
        total = merge(all_kinds)
        answer = merge(("final_answer",))
        sources = {u.source for _, u in self._usage}
        if not sources:
            source = "missing"
        elif sources == {"estimated"}:
            source = "estimated"
        elif sources == {"reported"}:
            source = "reported"
        elif "missing" in sources and sources <= {"missing"}:
            source = "missing"
        else:
            source = "mixed"
        return total, answer, source

    def metrics(self, budget_counters: dict, e2e_ms: float) -> dict:
        total_usage, answer_usage, usage_source = self.usage_summary()
        if not self._evidence:
            evidence = "non_stream"
        elif len(self._evidence) == 1:
            evidence = next(iter(self._evidence))
        else:
            evidence = "+".join(sorted(self._evidence))
        return {
            # v2 口径：e2e 为业务开始至终态实测时长
            "e2e_ms": round(e2e_ms, 3),
            # v2 起 ttft/tpot 不再用片段或配置值合成；保留字段并置 None
            "ttft_ms": None,
            "tpot_ms": None,
            "first_answer_ms": (
                round(self.first_answer_ms, 3)
                if self.first_answer_ms is not None else None
            ),
            "chunk_intervals_ms": list(self.chunk_intervals_ms),
            "answer_chunks": self.answer_chunks,
            "token_count": answer_usage.get("completion_tokens"),
            "total_tokens": total_usage.get("total_tokens"),
            "usage": total_usage,
            "answer_usage": answer_usage,
            "usage_source": usage_source,
            "evidence_kind": evidence,
            "iterations": self.decision_rounds,
            "tool_attempts": budget_counters.get("tool_attempts_used", 0),
        }

    def trace(self, budget_counters: dict) -> dict:
        total_usage, answer_usage, usage_source = self.usage_summary()
        return {
            "intent": self.intent,
            "decision_rounds": self.decision_rounds,
            "budget": dict(budget_counters),
            "usage": {"total": total_usage, "answer": answer_usage,
                      "source": usage_source},
            "tool_calls": list(self._observations),
            "rejections": list(self._rejections),
            "events": list(self._events),
            "answer_summary": {
                "chunks": self.answer_chunks,
                "chars": self.answer_chars,
                "first_answer_ms": (
                    round(self.first_answer_ms, 3)
                    if self.first_answer_ms is not None else None
                ),
                "chunk_intervals_ms": list(self.chunk_intervals_ms),
            },
        }


def _sum_or_none(values) -> Optional[int]:
    total = 0
    seen = False
    for v in values:
        if v is None:
            continue
        seen = True
        total += v
    return total if seen else None
