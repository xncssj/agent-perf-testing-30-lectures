"""Langfuse 观测导出器：把 Harness 运行事件导出到自托管 Langfuse。

设计约束（共享 Harness 设计说明 第 3.1、6.3 节）：
- 观测不干扰测量：事件在运行过程中仅做内存缓冲，运行进入终态后才
  异步批量上报（POST /api/public/ingestion，Basic 认证 pk/sk）；
  上报失败只记录日志，绝不影响业务运行与终态。
- 上报内容为查询、答复、用量、耗时、终态与工具轨迹，不包含完整
  Prompt 消息体与任何密钥。

映射关系（一次运行 = 一条 Trace）：
- RunResult → trace-create（input/output 为审计摘要（006 R06：默认不导出
  查询与答复原文），metadata 含 task_id/batch_id/status/stop_reason/证据类型，
  以及终态摘要：阶段三路径/分支/检索/节点数、阶段二规划/扇出/校验、
  三阶段通用的流式答复指标与护栏摘要；缺失字段自动省略；
  标签含 status/evidence，配置 stage 时附带阶段标识 stage:<阶段>；
  sessionId 只在存在真实 conversation_id 时填写，缺失留空，
  不用 batch_id 或 task_id 冒充会话（006 R03））；
- 每次模型调用（决策/最终答复）→ generation-create（含用量与来源；
  事件携带 parent_span_id 且指向非根 Trace 时写入 parentSpanId）；
- 每次工具尝试 → span-create（含 tool_call_id、attempt、耗时与错误；
  阶段二由子 Agent 调用产生时 parentSpanId 指向该 Agent span）；
- 阶段三节点执行边界 → span-create（name=node:<node_id>，含类型/耗时/重试）；
- 阶段二子 Agent 调度 → span-create（name=agent:<agent>，含状态/重试；
  start/end 取事件携带的真实起止墙钟，不再是同一时间戳）；
- 阶段二聚合校验 → span-create（name=validation，含校验结论摘要）。

上报属于旁路：失败不改业务终态，但必须留下受影响 run_id 与原因
（export_health 计数与最近失败清单，006 R02/V07）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from . import audit
from .logging_utils import get_logger
from .metrics import observe_exporter_flush

logger = get_logger("harness.langfuse")


def _iso(ts_wall: float | None) -> str:
    return datetime.fromtimestamp(ts_wall or time.time(), tz=timezone.utc).isoformat()


@dataclass
class LangfuseConfig:
    enabled: bool = False
    host: str = "http://localhost:3000"
    public_key: str = ""
    secret_key: str = ""
    timeout_s: float = 5.0
    stage: str = ""  # 阶段标识（如 s01/s02/s03），非空时写入 Trace 标签 stage:<阶段>

    def validate(self) -> None:
        if not self.enabled:
            return
        if not self.host:
            raise ValueError("LANGFUSE_ENABLED=true 需要配置 LANGFUSE_HOST")
        if not self.public_key or not self.secret_key:
            raise ValueError(
                "LANGFUSE_ENABLED=true 需要配置 LANGFUSE_PUBLIC_KEY 与 "
                "LANGFUSE_SECRET_KEY（pk-lf- / sk-lf- 开头）"
            )


@dataclass
class _RunBuffer:
    run_id: str
    task_id: str = ""
    batch_id: str = ""
    conversation_id: str = ""
    started_at: float = field(default_factory=time.time)
    query: str = ""
    output: str = ""
    result: dict = field(default_factory=dict)
    generations: list[dict] = field(default_factory=list)
    spans: list[dict] = field(default_factory=list)
    _pending_calls: dict = field(default_factory=dict)  # tool_call_id -> start 信息
    _seen_agent_spans: set = field(default_factory=set)  # (agent, span_id) 去重

    def note_call_start(self, event: dict) -> None:
        self._pending_calls[(event.get("tool_call_id"), event.get("attempt"))] = event

    def note_call_end(self, event: dict) -> None:
        key = (event.get("tool_call_id"), event.get("attempt"))
        start = self._pending_calls.pop(key, None)
        start_wall = (
            (start or {}).get("ts_wall") or event.get("ts_wall") or time.time()
        )
        parent_span_id = (
            event.get("parent_span_id")
            or (start or {}).get("parent_span_id")
            or ""
        )
        self.spans.append({
            "name": f"tool:{event.get('tool')}",
            "start_wall": start_wall,
            "end_wall": event.get("ts_wall") or time.time(),
            "parent_span_id": parent_span_id,
            "metadata": {
                "tool": event.get("tool"),
                "tool_call_id": event.get("tool_call_id"),
                "attempt": event.get("attempt"),
                "duration_ms": event.get("duration_ms"),
                "found": bool((event.get("data") or {}).get("found"))
                if not event.get("error") else None,
                # 只导出错误类别，不导出异常原文（006 R06：工具请求与结果
                # 默认不导出"异常原文"）
                "error_kind": event.get("error_kind") or (
                    "error" if event.get("error") else None
                ),
                "parent_span_id": parent_span_id,
            },
        })


class LangfuseExporter:
    """跨运行共享的 Langfuse 事件缓冲与批量上报器。"""

    def __init__(self, config: LangfuseConfig, client: httpx.AsyncClient | None = None) -> None:
        config.validate()
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.host.rstrip("/"),
            auth=(config.public_key, config.secret_key),
            timeout=config.timeout_s,
        )
        self._buffers: dict[str, _RunBuffer] = {}
        self._tasks: set[asyncio.Task] = set()
        # 导出健康信号（006 R02/V07）：上报失败不改业务终态，但必须留下
        # 受影响 run_id 与原因，避免把"没有 Trace"解释成"没有执行"。
        self.export_health: dict = {"success": 0, "failure": 0, "recent_failures": []}

    def _record_export_failure(self, run_id: str, reason: str) -> None:
        self.export_health["failure"] += 1
        recent = self.export_health["recent_failures"]
        recent.append({
            "run_id": run_id,
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        if len(recent) > 100:
            del recent[:-100]
        logger.warning("Langfuse 导出失败已记录 run_id=%s 原因=%s", run_id, reason)

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    # ---- 事件缓冲 ----
    def observe(self, event: dict) -> None:
        """登记一个事件（公共事件或内部 model_call 事件）；永不抛出。"""
        if not self._config.enabled or not event:
            return
        try:
            run_id = event.get("run_id") or ""
            if not run_id:
                return
            buf = self._buffers.get(run_id)
            if buf is None:
                buf = _RunBuffer(
                    run_id=run_id,
                    task_id=event.get("task_id") or "",
                    batch_id=event.get("batch_id") or "",
                    started_at=event.get("ts_wall") or time.time(),
                )
                self._buffers[run_id] = buf
            etype = event.get("type")
            if etype == "tool_call":
                buf.note_call_start(event)
            elif etype == "tool_result":
                buf.note_call_end(event)
            elif etype == "model_call":
                usage = event.get("usage") or {}
                # R3/P5：携带 start_wall（模型调用真实开始墙钟）时按真实区间
                # 换算 [start_wall, start_wall+latency]；缺省回退旧口径
                #（发射时刻起算，区间整体后移一个 latency——仅旧调用方未传时出现）
                gen_start = event.get("start_wall") or event.get("ts_wall")
                buf.generations.append({
                    "name": event.get("call_kind") or "model_call",
                    "start_wall": gen_start,
                    "end_wall": (gen_start or time.time())
                    + (event.get("latency_ms") or 0) / 1000.0,
                    "model": event.get("model") or "",
                    "parent_span_id": event.get("parent_span_id") or "",
                    "usage": {
                        "input": usage.get("prompt_tokens"),
                        "output": usage.get("completion_tokens"),
                        "total": usage.get("total_tokens"),
                        "unit": "TOKENS",
                    }
                    if usage.get("total_tokens") is not None else None,
                    "usage_source": usage.get("source"),
                })
            elif etype == "node":
                # 阶段三节点执行边界： startTime 由耗时反推，非 completed 标 WARNING
                end_wall = event.get("ts_wall") or time.time()
                status = event.get("status")
                buf.spans.append({
                    "name": f"node:{event.get('node_id')}",
                    "start_wall": end_wall - (event.get("latency_ms") or 0) / 1000.0,
                    "end_wall": end_wall,
                    "metadata": {
                        "node_type": event.get("node_type"),
                        "status": status,
                        "latency_ms": event.get("latency_ms"),
                        "attempts": event.get("attempts"),
                    },
                    "level": None if status == "completed" else "WARNING",
                })
            elif etype == "agent_invoke":
                # 阶段二子 Agent 调度结果：失败态（超时/错误/熔断跳过）标 ERROR；
                # 上游可能对同一次调度（同 span_id）重复发事件，同一 span 只映射一次；
                # start/end 取事件携带的真实起止墙钟（缺省回退事件墙钟），
                # 不再用同一时间戳伪装耗时（006 R02/G03）
                key = ("agent", event.get("agent"), event.get("span_id"))
                if key in buf._seen_agent_spans:
                    return
                buf._seen_agent_spans.add(key)
                wall = event.get("ts_wall") or time.time()
                status = event.get("status")
                buf.spans.append({
                    "name": f"agent:{event.get('agent')}",
                    "start_wall": event.get("start_wall") or wall,
                    "end_wall": event.get("end_wall") or wall,
                    "metadata": {
                        "status": status,
                        "attempts": event.get("attempts"),
                        "span_id": event.get("span_id"),
                        "parent_span_id": event.get("parent_span_id") or "",
                        "error_kind": event.get("error_kind"),
                    },
                    "level": (
                        "ERROR"
                        if status in ("timeout", "error", "skipped_breaker")
                        else None
                    ),
                })
            elif etype == "validation":
                # 阶段二聚合校验结论
                wall = event.get("ts_wall") or time.time()
                data = event.get("validation")
                buf.spans.append({
                    "name": "validation",
                    "start_wall": wall,
                    "end_wall": wall,
                    "metadata": data if isinstance(data, dict) else {},
                })
            elif etype == "done":
                buf.result = event
                buf.output = event.get("response") or ""
        except Exception:  # noqa: BLE001  观测失败不影响业务
            logger.debug("Langfuse 事件缓冲失败", exc_info=True)

    # ---- 上报 ----
    def schedule_flush(self, run_id: str, query: str = "",
                       conversation_id: str = "") -> None:
        """终态后异步上报；由服务层在运行结束时调用。

        conversation_id 为请求显式提供的会话关联 ID；缺失保持为空，
        不用 batch_id/task_id 冒充会话（006 R03）。
        """
        if not self._config.enabled:
            self._buffers.pop(run_id, None)
            return
        buf = self._buffers.get(run_id)
        if buf is None:
            return
        if query:
            buf.query = buf.query or query
        if conversation_id:
            buf.conversation_id = buf.conversation_id or conversation_id
        task = asyncio.create_task(self._flush_safe(run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def flush_now(self, run_id: str, query: str = "",
                        conversation_id: str = "") -> dict | None:
        """同步等待的上报入口（测试与进程收尾使用）。"""
        buf = self._buffers.get(run_id)
        if buf is None:
            return None
        if query:
            buf.query = buf.query or query
        if conversation_id:
            buf.conversation_id = buf.conversation_id or conversation_id
        return await self._flush_safe(run_id)

    async def _flush_safe(self, run_id: str) -> dict | None:
        buf = self._buffers.pop(run_id, None)
        if buf is None:
            return None
        # R3/B13：上报开销量化——flush 全程计时（含序列化与 HTTP 往返），
        # 成败入 result 标签；异步批任务路径与 flush_now 共用本方法
        flush_started = time.perf_counter()
        flush_ok = False
        try:
            payload = self._build_payload(buf)
            resp = await self._client.post("/api/public/ingestion", json=payload)
            if resp.status_code >= 400:
                reason = f"HTTP {resp.status_code}: {resp.text[:200]}"
                self._record_export_failure(run_id, reason)
                return None
            # 多状态响应（如 207）会携带逐条结果：被拒事件必须显式告警，
            # 不允许静默丢弃（例如 v4 events_only 模式拒绝旧版事件）
            accepted = len(payload["batch"])
            rejected = 0
            try:
                body = resp.json()
            except ValueError:
                body = {}
            for err in (body or {}).get("errors") or []:
                rejected += 1
                logger.warning(
                    "Langfuse 事件被拒 run_id=%s id=%s HTTP %s: %s",
                    run_id, err.get("id"), err.get("status"),
                    (err.get("error") or err.get("message") or "")[:200],
                )
            if rejected and not (body or {}).get("successes"):
                reason = f"全部被拒（{rejected} 条）"
                self._record_export_failure(run_id, reason)
                return None
            self.export_health["success"] += 1
            flush_ok = True
            return {"status": resp.status_code, "items": accepted - rejected}
        except Exception as exc:  # noqa: BLE001  上报失败不影响业务
            self._record_export_failure(run_id, f"异常: {exc}")
            return None
        finally:
            observe_exporter_flush(time.perf_counter() - flush_started, flush_ok)

    def _build_payload(self, buf: _RunBuffer) -> dict:
        """构建 Langfuse Ingestion 批量载荷（v4 信封格式，实测 4.35.0）。

        每个事件为 {id, type, timestamp, body}：timestamp 为事件时间的 ISO 字符串，
        事件字段全部放入 body（旧版平铺格式会被 v4 以 400 拒绝）。
        """
        result = buf.result or {}
        metrics = result.get("metrics") or {}
        guard = metrics.get("guardrail") or (result.get("trace") or {}).get("guardrail")
        # 006 R06：input/output 走审计摘要（长度区间/意图/终态/护栏判定），
        # 查询与答复原文默认不导出；需要原文复核时用受控实验产物。
        trace_input = audit.summarize_input(buf.query, intent=result.get("intent"))
        trace_output = audit.summarize_answer(
            buf.output, stop_reason=result.get("stop_reason"), guardrail=guard,
        )
        trace_meta = {
            "schema_version": result.get("schema_version", 2),
            "task_id": buf.task_id,
            "batch_id": buf.batch_id,
            "conversation_id": buf.conversation_id or None,
            "status": result.get("status"),
            "stop_reason": result.get("stop_reason"),
            "intent": result.get("intent"),
            "error": result.get("error"),
            "evidence_kind": metrics.get("evidence_kind"),
            "usage_source": metrics.get("usage_source"),
            "total_tokens": metrics.get("total_tokens"),
            "e2e_ms": metrics.get("e2e_ms"),
        }
        trace_meta.update(self._stage_metadata(metrics, result.get("trace") or {}))
        status = result.get("status") or "unknown"
        tags = [f"status:{status}",
                f"evidence:{metrics.get('evidence_kind')}"]
        if self._config.stage:
            tags.append(f"stage:{self._config.stage}")
        batch: list[dict] = [{
            "id": buf.run_id,
            "type": "trace-create",
            "timestamp": _iso(buf.started_at),
            "body": {
                "id": buf.run_id,
                "timestamp": _iso(buf.started_at),
                "name": "customer-service-run",
                "input": trace_input,
                "output": trace_output,
                # 会话语义（006 R03）：只在存在真实 conversation_id 时填写，
                # 缺失留空，不用 batch_id/task_id 冒充会话
                "sessionId": buf.conversation_id or None,
                "tags": tags,
                "metadata": trace_meta,
            },
        }]
        for gen in buf.generations:
            gen_id = uuid.uuid4().hex
            body = {
                "id": gen_id,
                "timestamp": _iso(gen["start_wall"]),
                "traceId": buf.run_id,
                "name": gen["name"],
                "startTime": _iso(gen["start_wall"]),
                "endTime": _iso(gen["end_wall"]),
                "model": gen.get("model") or None,
                "metadata": {"usage_source": gen.get("usage_source")},
            }
            if gen.get("usage"):
                body["usage"] = gen["usage"]
            parent = gen.get("parent_span_id") or ""
            if parent and parent != buf.run_id:
                body["parentSpanId"] = parent
            batch.append({
                "id": gen_id, "type": "generation-create",
                "timestamp": _iso(gen["start_wall"]), "body": body,
            })
        for span in buf.spans:
            meta = dict(span.get("metadata") or {})
            level = span.get("level") or (
                "ERROR"
                if (meta.get("error") or meta.get("error_kind"))
                else "DEFAULT"
            )
            # span 身份与父链（006 R02）：携带业务 span_id 的 observation
            # （子 Agent）以该 span_id 作为 Langfuse id，工具与嵌套
            # observation 的 parentSpanId 才能解析到真实父步骤；
            # 未携带 span_id 的 span（节点/工具/校验）仍用随机 id。
            langfuse_id = meta.get("span_id") or uuid.uuid4().hex
            body = {
                "id": langfuse_id,
                "timestamp": _iso(span["start_wall"]),
                "traceId": buf.run_id,
                "name": span["name"],
                "startTime": _iso(span["start_wall"]),
                "endTime": _iso(span["end_wall"]),
                "level": level,
                "metadata": meta,
            }
            parent = span.get("parent_span_id") or ""
            if parent and parent != buf.run_id:
                # 父步骤指向真实父 span；仅同属一个 Trace 不视作父子
                body["parentSpanId"] = parent
            batch.append({
                "id": langfuse_id,
                "type": "span-create",
                "timestamp": _iso(span["start_wall"]),
                "body": body,
            })
        return {"batch": batch, "metadata": {"sdk": "tech-features-harness"}}

    @staticmethod
    def _stage_metadata(metrics: dict, trace: dict) -> dict:
        """从终态 metrics/trace 提取阶段摘要；缺失或为空即省略，不占位。"""
        if not isinstance(metrics, dict):
            metrics = {}
        if not isinstance(trace, dict):
            trace = {}
        extra: dict = {}
        # 流式答复证据（三阶段通用，来自 RunRecorder.metrics）
        if metrics.get("first_answer_ms") is not None:
            extra["first_answer_ms"] = metrics.get("first_answer_ms")
        if metrics.get("answer_chunks"):
            extra["answer_chunks"] = metrics.get("answer_chunks")
        if metrics.get("chunk_intervals_ms"):
            extra["chunk_intervals_ms"] = metrics.get("chunk_intervals_ms")
        # 护栏摘要（阶段二在 metrics，阶段三在 trace；同一共享实现）
        guard = metrics.get("guardrail") or trace.get("guardrail") or {}
        if isinstance(guard, dict) and guard:
            violations = guard.get("violations")
            extra["guardrail"] = {
                "blocked": bool(guard.get("blocked")),
                "violations": (
                    len(violations) if isinstance(violations, list) else violations
                ),
            }
        # 阶段三：workflow 路径、分支与检索摘要
        if trace.get("path"):
            extra["path"] = trace["path"]
        if trace.get("branch"):
            extra["branch"] = trace["branch"]
        if trace.get("confidence") is not None:
            extra["confidence"] = trace["confidence"]
        retrieval = trace.get("retrieval")
        if isinstance(retrieval, dict) and retrieval:
            summary: dict = {}
            for key in ("top_k", "latency_ms", "corpus", "index_version"):
                if retrieval.get(key) is not None:
                    summary[key] = retrieval[key]
            hits = retrieval.get("hits")
            summary["hits"] = len(hits) if isinstance(hits, list) else hits
            extra["retrieval"] = summary
        for key in ("citations", "node_executions", "node_exec_errors"):
            value = trace.get(key)
            if value:
                extra[key] = len(value) if isinstance(value, list) else value
        # 阶段二：编排摘要
        if trace.get("plan_agents"):
            extra["plan_agents"] = trace["plan_agents"]
        if trace.get("degraded") is not None:
            extra["degraded"] = trace["degraded"]
        if trace.get("degraded_agents"):
            extra["degraded_agents"] = trace["degraded_agents"]
        fanout = trace.get("fanout")
        if isinstance(fanout, dict) and fanout:
            extra["fanout"] = {
                k: fanout[k]
                for k in ("round_wall_ms", "serial_sum_ms", "efficiency", "agents")
                if fanout.get(k) is not None
            }
        validation = trace.get("validation")
        if isinstance(validation, dict) and validation:
            extra["validation"] = {
                k: validation[k]
                for k in ("status", "missing", "fallback", "latency_ms", "tokens")
                if validation.get(k) is not None
            }
        return extra

    # ---- 生命周期 ----
    async def aclose(self, timeout_s: float = 5.0) -> None:
        """等待已调度的上报完成并释放连接（进程收尾调用）。"""
        if self._tasks:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*list(self._tasks), return_exceptions=True),
                    timeout=timeout_s,
                )
        await self._client.aclose()


def dump_payload_preview(payload: dict) -> str:
    """调试辅助：输出上报载荷的摘要（不含业务文本全文）。"""
    items = payload.get("batch") or []
    return json.dumps(
        [{"type": i.get("type"), "name": i.get("name")} for i in items],
        ensure_ascii=False,
    )
