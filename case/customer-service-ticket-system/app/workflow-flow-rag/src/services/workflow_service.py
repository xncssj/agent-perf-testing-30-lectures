"""WorkflowService：组装数据、工具、RAG、LLM 与执行器，对外提供统一处理入口。

v2（共享 Harness 设计说明 第 8.2 节）：保留 WorkflowExecutor 的 DAG 结构，在节点执行
边界接入身份、预算与事件（scope 注入）；对外提供与阶段一/二同构的
arun/astream 运行入口（RunInput → RunResult + 事件流），Langfuse 一次运行
一条 Trace（模型调用节点为 GENERATION、工具尝试为 SPAN、节点为带 node_id 的事件）。

已知边界（如实声明）：检查点默认 memory 后端，仅进程内存活范围内可续跑；
配置 sqlite 后端后跨进程/容器重启可续跑，completed 终态按保留策略清理
（配置见 workflow_config.yaml checkpoint 节，课程正文对应段落）。
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from prometheus_client import Counter, Gauge

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.budget import Budget, BudgetExceeded, DeadlineExceeded  # noqa: E402
from harness.contracts import RunInput, RunResult  # noqa: E402
from harness.langfuse_exporter import LangfuseConfig, LangfuseExporter  # noqa: E402
from harness.observation import RunRecorder  # noqa: E402
from harness.registry import ToolRegistry as SharedToolRegistry  # noqa: E402
from harness.tool_executor import ToolExecutor  # noqa: E402

from ..config.settings import Settings  # noqa: E402
from ..llm.mock_llm import MockLlm  # noqa: E402
from ..llm.openai_llm import OpenAILlm  # noqa: E402
from ..models.store import DataStore  # noqa: E402
from ..rag.engine import RagEngine  # noqa: E402
from ..tools.data_tools import QueryLogisticsTool, QueryOrderTool  # noqa: E402
from ..tools.registry import ToolRegistry  # noqa: E402
from ..utils.logger import get_logger  # noqa: E402
from ..utils.version import build_run_fingerprint, new_run_id  # noqa: E402
from ..workflow.engine import (  # noqa: E402
    LoopBudgetExceeded,
    NodeError,
    WorkflowExecutor,
)
from ..workflow.graph import build_graph  # noqa: E402
from ..workflow.nodes import WorkflowNodes  # noqa: E402

logger = get_logger("services.workflow")

RUN_TERMINAL_TOTAL = Counter(
    "workflow_run_terminal_total",
    "Workflow 运行终态计数",
    ["status", "stop_reason"],
)

# 在途业务数：容量观测口径（实际在途 vs 施加负载，见课程第 06 讲）
RUNS_INFLIGHT = Gauge(
    "workflow_runs_inflight",
    "当前执行中的业务运行数（含流式）",
)

_PUMP_DONE = object()

# 分支 -> 终态 stop_reason
_BRANCH_STOP = {
    "clarification": "clarification",
    "human_transfer": "handoff_requested",
    "auto_reply": "answered",
}


def _map_error(exc: BaseException) -> str:
    """线程内异常 -> 终态映射字符串（status: stop_reason）。"""
    if isinstance(exc, BudgetExceeded):
        return f"budget_exhausted: {exc}"
    if isinstance(exc, DeadlineExceeded):
        return "timed_out: request_deadline"
    if isinstance(exc, LoopBudgetExceeded):
        return f"budget_exhausted: {exc}"
    if isinstance(exc, NodeError):
        return f"tool_failed: {exc}"
    return f"internal_error: {type(exc).__name__}: {exc}"


class KnowledgeNotFoundError(LookupError):
    """知识文档不存在（HTTP 层映射 404）。"""


class KnowledgeConflictError(ValueError):
    """category:title 文档身份冲突（HTTP 层映射 409）。"""


class WorkflowService:
    def __init__(self, settings=None) -> None:
        settings = settings or Settings()
        self.settings = settings
        self.store = DataStore(settings.data_dir)
        # 知识库写锁：覆盖同进程所有知识库写入口，串行化候选构建与发布
        self._kb_write_lock = threading.Lock()
        self.tools = ToolRegistry(self.store, settings.tool_config)
        self.rag = RagEngine(self.store, top_k=settings.top_k,
                             knowledge_base=settings.knowledge_base)
        self.rag.warm()

        provider = (settings.llm_provider or "mock").lower()
        if provider == "openai":
            self.llm = OpenAILlm(settings)
            logger.info("LLM provider=openai (%s)", settings.model_name)
        else:
            self.llm = MockLlm(settings)
            logger.info("LLM provider=mock (seed=%s)", settings.mock_seed)

        # v2：共享有界工具执行器（sync 模式，整个 workflow 在工作线程内执行）
        self.shared_registry = SharedToolRegistry()
        self.shared_registry.register(
            QueryOrderTool(self.store,
                           (settings.tool_config or {}).get("query_order")),
            enabled=bool((settings.tool_config or {}).get("query_order", {})
                         .get("enabled", True)),
        )
        self.shared_registry.register(
            QueryLogisticsTool(self.store,
                               (settings.tool_config or {}).get("query_logistics")),
            enabled=bool((settings.tool_config or {}).get("query_logistics", {})
                         .get("enabled", True)),
        )
        self.tool_executor = ToolExecutor(
            self.shared_registry,
            tool_workers=settings.tool_workers,
            tool_queue_size=settings.tool_queue_size,
            sync_mode=True,
        )

        nodes, edges, start = build_graph()
        self.nodes = WorkflowNodes(self.llm, self.tools, self.rag, settings)
        self.nodes.tool_executor = self.tool_executor
        self.executor = WorkflowExecutor(
            nodes=nodes, edges=edges, start_node=start,
            node_runner=self.nodes.runner(), settings=settings,
        )
        self.exporter = self._build_exporter()
        self.fingerprint = build_run_fingerprint(
            Path(__file__).resolve().parents[2], settings
        )
        logger.info(
            "Workflow 就绪: 节点=%d, 边=%d, KB=%d, 订单=%d, 物流=%d",
            len(nodes), len(edges), len(self.store.knowledge_docs()),
            len(self.store._orders), len(self.store._logistics_by_order),
        )

    def _build_exporter(self) -> LangfuseExporter | None:
        s = self.settings
        if not s.langfuse_enabled:
            return None
        return LangfuseExporter(LangfuseConfig(
            enabled=True, host=s.langfuse_host,
            public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key,
            timeout_s=s.langfuse_timeout_s,
            stage="s03",
        ))

    # ---- v1 兼容入口（既有压测/门禁脚本继续使用）----
    def process(self, query: str, execution_id: str | None = None) -> dict:
        return self.executor.execute(query, execution_id=execution_id)

    # ---- 知识库写入口（课程知识库写入口：统一经服务层串行提交）----

    def create_knowledge(self, category: str, title: str, content: str,
                         keywords: list[str] | None = None) -> dict:
        """新增知识文档；重复 category:title 抛 KnowledgeConflictError。"""

        def apply(docs: list[dict], next_id: int) -> tuple[dict, int]:
            doc = {
                "id": next_id,
                "category": category,
                "title": title,
                "content": content,
                "keywords": list(keywords or []),
            }
            docs.append(doc)
            return doc, next_id + 1

        return self._commit_knowledge(apply)

    def create_knowledge_batch(self, category: str,
                               chunks: list[tuple[str, str]]) -> list[dict]:
        """整批写入（上传分块）：全部分块一次提交，任一失败整批不生效。"""

        def apply(docs: list[dict], next_id: int) -> tuple[list[dict], int]:
            created: list[dict] = []
            for title, content in chunks:
                doc = {
                    "id": next_id,
                    "category": category,
                    "title": title,
                    "content": content,
                    "keywords": [],
                }
                docs.append(doc)
                created.append(doc)
                next_id += 1
            return created, next_id

        return self._commit_knowledge(apply)

    def update_knowledge(self, kid: int, category: str | None = None,
                         title: str | None = None, content: str | None = None,
                         keywords: list[str] | None = None) -> dict:
        """按数字 ID 更新；不存在抛 KnowledgeNotFoundError，
        更新后与其他文档重名抛 KnowledgeConflictError（原文档保持不变）。"""

        def apply(docs: list[dict], next_id: int) -> tuple[dict, int]:
            for i, doc in enumerate(docs):
                if doc.get("id") == kid:
                    updated = dict(doc)
                    if category is not None:
                        updated["category"] = category
                    if title is not None:
                        updated["title"] = title
                    if content is not None:
                        updated["content"] = content
                    if keywords is not None:
                        updated["keywords"] = list(keywords)
                    docs[i] = updated  # 新副本替换，不原位修改已发布文档
                    return updated, next_id
            raise KnowledgeNotFoundError(f"知识文档不存在: {kid}")

        return self._commit_knowledge(apply)

    def delete_knowledge(self, kid: int) -> dict:
        """按数字 ID 删除；不存在抛 KnowledgeNotFoundError。"""

        def apply(docs: list[dict], next_id: int) -> tuple[dict, int]:
            for i, doc in enumerate(docs):
                if doc.get("id") == kid:
                    docs.pop(i)
                    return doc, next_id
            raise KnowledgeNotFoundError(f"知识文档不存在: {kid}")

        return self._commit_knowledge(apply)

    def _commit_knowledge(self, apply_changes: Callable) -> Any:
        """知识库写统一提交路径（服务层写锁内串行）：

        复制现有文档 → 应用整批变更 → 校验 category:title 唯一
        → 构建候选索引并发布快照 → 替换内存文档 → 返回。
        校验或候选索引构建失败时抛出异常：内存文档、下一个可用 ID 与
        已发布快照均保持写入前状态（失败不部分写入）。候选文档均为
        副本，不原位修改已被并发检索持有的旧索引内容。
        """
        with self._kb_write_lock:
            docs = self.store.knowledge_docs()
            next_id = self.store.next_knowledge_id()
            result, next_id = apply_changes(docs, next_id)

            # 写入前校验文档身份唯一，不依赖索引字典覆盖处理冲突
            seen: set[str] = set()
            for d in docs:
                did = DataStore.doc_id(d)
                if did in seen:
                    raise KnowledgeConflictError(f"重复知识文档: {did}")
                seen.add(did)

            # 候选文档副本携带 doc_id；构建失败抛异常时未发布任何内容
            candidate = []
            for d in docs:
                item = dict(d)
                item["doc_id"] = DataStore.doc_id(d)
                candidate.append(item)
            self.rag.publish(candidate)
            self.store.replace_knowledge(docs, next_id)
            return result

    def graph_snapshot(self) -> dict:
        nodes, edges, start = build_graph()
        return {
            "start": start,
            "nodes": [{"id": n.id, "type": n.type, "name": n.name} for n in nodes],
            "edges": [{"source": e.source, "target": e.target,
                       "condition": e.condition, "parallel": e.parallel}
                      for e in edges],
        }

    def health(self) -> dict:
        return {
            "status": "ok",
            "provider": self.settings.llm_provider,
            "kb_docs": len(self.store.knowledge_docs()),
            "orders": len(self.store._orders),
            "logistics": len(self.store._logistics_by_order),
        }

    # ---- v2 运行入口（与阶段一/二同构：RunInput → RunResult / 事件流）----
    async def arun(self, run_input: RunInput) -> RunResult:
        """非流式执行一笔业务，返回终态 RunResult。"""
        result_dict: dict = {}
        sink = self.exporter.observe if self.exporter else None
        RUNS_INFLIGHT.inc()
        try:
            async for event in self._run_stream(run_input, internal_sink=sink):
                if self.exporter:
                    self.exporter.observe(event)
                if event.get("type") == "done":
                    self._count_terminal(event)
                result_dict = event
            result = RunResult(**{
                k: v for k, v in result_dict.items() if k in RunResult.model_fields
            })
            if self.exporter:
                self.exporter.schedule_flush(
                    result.run_id, query=run_input.query,
                    conversation_id=run_input.conversation_id,
                )
            return result
        finally:
            RUNS_INFLIGHT.dec()

    async def astream(self, run_input: RunInput):
        """流式执行：节点/工具事件实时产出；答复按 Mock 节奏流出；done 承载终态。"""
        queue: asyncio.Queue = asyncio.Queue()
        last_event: dict = {}
        pump_error: list[BaseException] = []
        sink = self.exporter.observe if self.exporter else None
        RUNS_INFLIGHT.inc()

        async def pump() -> None:
            try:
                async for event in self._run_stream(run_input, internal_sink=sink):
                    last_event.clear()
                    last_event.update(event)
                    if self.exporter:
                        self.exporter.observe(event)
                    queue.put_nowait(event)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001  原样透传给消费端
                pump_error.append(exc)
            finally:
                queue.put_nowait(_PUMP_DONE)

        pump_task = asyncio.create_task(pump(), name="flow-astream-pump")
        try:
            while True:
                event = await queue.get()
                if event is _PUMP_DONE:
                    if pump_error:
                        raise pump_error[0]
                    return
                if event.get("type") == "done":
                    self._count_terminal(event)
                    if self.exporter:
                        self.exporter.schedule_flush(
                            event.get("run_id", ""), query=run_input.query,
                            conversation_id=run_input.conversation_id,
                        )
                yield event
        except GeneratorExit:
            self._on_cancelled(run_input, last_event)
            raise
        except asyncio.CancelledError:
            self._on_cancelled(run_input, last_event)
            raise
        finally:
            self._cancel_pump(pump_task)
            RUNS_INFLIGHT.dec()

    @staticmethod
    def _cancel_pump(pump_task: asyncio.Task) -> None:
        if not pump_task.done():
            pump_task.cancel()

    def _on_cancelled(self, run_input: RunInput, last_event: dict) -> None:
        RUN_TERMINAL_TOTAL.labels(
            status="cancelled", stop_reason="client_disconnect").inc()
        if self.exporter and last_event.get("run_id"):
            self.exporter.observe({
                "run_id": last_event.get("run_id"),
                "task_id": last_event.get("task_id", ""),
                "batch_id": last_event.get("batch_id", ""),
                "type": "done",
                "schema_version": 2,
                "status": "cancelled",
                "stop_reason": "client_disconnect",
                "response": "",
                "metrics": {},
            })
            self.exporter.schedule_flush(
                last_event.get("run_id"), query=run_input.query,
                conversation_id=run_input.conversation_id,
            )
        logger.warning("SSE 消费端断连 run_id=%s", last_event.get("run_id", ""))

    def _count_terminal(self, event: dict) -> None:
        RUN_TERMINAL_TOTAL.labels(
            status=str(event.get("status", "unknown")),
            stop_reason=str(event.get("stop_reason", "")),
        ).inc()

    # ---- 生命周期 ----
    async def aclose(self) -> None:
        await self.tool_executor.aclose()
        if self.exporter:
            await self.exporter.aclose()

    # ---- 元信息 ----
    def versions(self) -> dict:
        payload = dict(self.fingerprint)
        payload["protocol_mode"] = {
            "llm_provider": self.settings.llm_provider,
            "orchestration": "workflow+rag",
            "rag_index_version": self.rag.index_version,
        }
        payload["effective_limits"] = {
            "max_iterations": self.settings.max_iterations,
            "max_node_executions": self.settings.max_node_executions,
            "max_tool_calls": self.settings.max_tool_calls,
            "request_timeout_s": self.settings.request_timeout,
            "tool_workers": self.settings.tool_workers,
            "tool_queue_size": self.settings.tool_queue_size,
            "top_k": self.settings.top_k,
        }
        payload["observability"] = {"langfuse_enabled": bool(self.exporter)}
        payload["checkpoint"] = {
            "enabled": self.settings.checkpoint_enabled,
            # 如实声明可恢复范围：memory 仅进程内存活期内可续跑；
            # sqlite 跨进程/容器重启可续跑（检查点生命周期见 workflow_config.yaml）
            "store": self.settings.checkpoint_store,
            "purge_on_completed": getattr(
                self.settings, "checkpoint_purge_on_completed", True),
        }
        return payload

    def new_batch_id(self) -> str:
        return new_run_id(self.fingerprint.get("fingerprint"))

    # ---- 内部：单次运行（业务线程 + 事件桥 + 答复直出）----
    async def _run_stream(self, run_input: RunInput, internal_sink=None):
        """单次运行的异步生成器：节点/工具事件实时产出，答复按 Mock 节奏直出。

        业务 workflow 在 to_thread 工作线程内执行（recorder 跨线程安全发布）；
        事件经队列桥接，线程结束后由本生成器流出答复并产出 done。
        """
        from harness.contracts import RunContext, new_id

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        timeout_s = float(self.settings.request_timeout)
        ctx = RunContext(
            task_id=run_input.task_id or new_id(),
            run_id=new_id(),
            batch_id=run_input.batch_id,
            conversation_id=run_input.conversation_id,
            query=run_input.query,
            deadline=loop.time() + timeout_s,
        )
        budget = Budget(
            max_iterations=int(self.settings.max_iterations),
            max_tool_calls=int(self.settings.max_tool_calls),
            timeout_s=timeout_s,
        )
        recorder = RunRecorder(ctx, queue=queue, internal_sink=internal_sink)
        started_epoch_ms = int(time.time() * 1000)

        def check_deadline() -> None:
            if loop.time() >= ctx.deadline:
                raise DeadlineExceeded()

        def record_node(node_id, node_type, latency, status, attempts) -> None:
            # 工作线程调用：recorder 内部跨线程安全发布
            recorder.emit_node(node_id, node_type, latency, status, attempts)

        scope = {
            "recorder": recorder,
            "ctx": ctx,
            "budget": budget,
            "check_deadline": check_deadline,
            "record_node": record_node,
            "tool_executor": self.tool_executor,
        }

        def business() -> dict:
            return self.executor.execute(
                run_input.query,
                execution_id=run_input.conversation_id or None,
                scope=scope,
            )

        def emit(ev: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, ev)

        yield recorder.build_public_event(
            "thinking", content="Workflow 正在按节点编排执行..."
        )
        thread_task = loop.run_in_executor(None, business)

        raw: dict = {}
        error: str | None = None
        cancelled = False
        try:
            while True:
                get_task = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait(
                    {get_task, thread_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if get_task in done:
                    event = get_task.result()
                    yield event
                else:
                    # 线程先结束：排空剩余事件后收敛
                    get_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await get_task
                    with contextlib.suppress(asyncio.QueueEmpty):
                        while True:
                            yield queue.get_nowait()
                    try:
                        raw = thread_task.result()
                    except BaseException as exc:  # noqa: BLE001
                        error = _map_error(exc)
                    break
        except asyncio.CancelledError:
            cancelled = True
            thread_task.cancel()
            with contextlib.suppress(Exception):
                await thread_task
            result = self._build_result(
                ctx, budget, recorder, {}, "cancelled", "client_disconnect",
                None, started_epoch_ms,
            )
            logger.warning("运行取消 run_id=%s", ctx.run_id)
            recorder.emit_done({
                "status": result.status, "stop_reason": result.stop_reason,
                "response": "", "intent": "", "error": None,
                "metrics": result.metrics, "trace": result.trace,
            })
            raise
        finally:
            if not thread_task.done() and not cancelled:
                thread_task.cancel()

        if error is not None:
            status, _, stop_reason = error.partition(": ")
            stop_reason = stop_reason or error
        else:
            raw_status = raw.get("status") or "completed"
            if raw_status == "completed":
                branch = raw.get("branch") or ""
                status = "completed"
                stop_reason = _BRANCH_STOP.get(branch, "answered")
            else:
                # 执行器自身分类的失败/预算/超时终态（NodeError、循环预算等）
                status = raw_status
                raw_error = raw.get("error") or ""
                if raw_status == "timed_out":
                    stop_reason = "request_deadline"
                elif raw_status == "budget_exhausted":
                    stop_reason = ("max_node_executions"
                                   if "循环预算" in raw_error else "max_tool_calls")
                else:
                    stop_reason = f"tool_failed: {raw_error}"

        # 答复流出（Mock 节奏）：事件直出，不经队列
        response = raw.get("response") or ""
        if response and status == "completed":
            recorder.mark_evidence(
                "mock" if self.settings.llm_provider == "mock" else "provider_stream"
            )
        for ch in response:
            if ctx.remaining_s() <= 0:
                status, stop_reason = "timed_out", "request_deadline"
                break
            recorder.record_answer_only(ch, source="model")
            yield recorder.build_public_event("answer", content=ch, source="model")
            await asyncio.sleep(self.settings.token_latency_ms / 1000.0)
        if status == "completed" and not response.strip():
            status, stop_reason = "failed", "empty_answer"

        recorder.intent = raw.get("intent", "")
        result = self._build_result(
            ctx, budget, recorder, raw, status, stop_reason,
            error if status != "completed" else None, started_epoch_ms,
        )
        yield recorder.build_public_event(
            "done",
            status=result.status,
            stop_reason=result.stop_reason,
            response=result.response,
            intent=result.intent,
            error=result.error,
            metrics=result.metrics,
            trace=result.trace,
        )
        logger.info(
            "运行终态 %s", {
                "run_id": result.run_id, "task_id": result.task_id,
                "status": result.status, "stop_reason": result.stop_reason,
                "branch": result.trace.get("branch"),
                "total_tokens": result.metrics.get("total_tokens"),
            }.__str__()
        )

    def _build_result(
        self,
        ctx,
        budget: Budget,
        recorder: RunRecorder,
        raw: dict,
        status: str,
        stop_reason: str,
        error: str | None,
        started_epoch_ms: int,
    ) -> RunResult:
        counters = budget.counters()
        e2e_ms = recorder.elapsed_ms()
        metrics = recorder.metrics(counters, e2e_ms)
        trace = recorder.trace(counters)
        raw = raw or {}
        trace.update({
            "path": raw.get("path", []),
            "branch": raw.get("branch", ""),
            "intent": raw.get("intent", ""),
            "confidence": raw.get("confidence", 0.0),
            "degraded": bool((raw.get("facts") or {}).get("degraded", {}).get("marked")),
            "retrieval": raw.get("retrieval", {}),
            "citations": raw.get("citations", []),
            "guardrail": raw.get("guardrail", {}),
            "node_executions": raw.get("node_executions", []),
            "node_exec_errors": raw.get("node_exec_errors", []),
            "tool_calls": [
                {"tool": tc.get("tool"), "params": tc.get("params"),
                 "found": tc.get("found"), "latency_ms": tc.get("latency_ms"),
                 "error": tc.get("error")}
                for tc in raw.get("tool_calls", [])
            ],
        })
        # 评测脚本按 v1 兼容方式读取 metrics["trace"]（与顶层 trace 同一结构）
        metrics["trace"] = trace
        return RunResult(
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            batch_id=ctx.batch_id,
            status=status,
            stop_reason=stop_reason,
            response=raw.get("response", ""),
            intent=raw.get("intent", ""),
            error=error,
            started_at_epoch_ms=started_epoch_ms,
            ended_at_epoch_ms=int(time.time() * 1000),
            metrics=metrics,
            trace=trace,
        )
