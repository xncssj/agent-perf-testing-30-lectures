"""服务组装：把配置、数据、工具、Provider 与 Harness 运行器装配为服务对象。

v2 装配（共享 Harness 设计说明 第 3.2、6.2 节）：
- 统一依赖组装：注册表（含禁用控制）→ 有界工具执行器 → Provider
  （Mock / OpenAI 原生 / JSON 兼容）→ 单一异步运行器；
- 事件分接：SSE 公共事件、运行终态计数、Langfuse 观测缓冲互不阻塞；
- 保留同步兼容入口 process()，仅供没有运行中事件循环的调用方使用。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from prometheus_client import Counter, Gauge

from ..agent.prompt_manager import PromptManager
from ..config.settings import Settings
from ..harness.langfuse_exporter import LangfuseConfig, LangfuseExporter
from ..harness.runtime import HarnessRunner
from ..harness.tool_executor import ToolExecutor
from ..llm.base import BaseProvider
from ..llm.mock_provider import MockProvider
from ..harness.contracts import RunInput, RunResult
from ..tools.knowledge_tool import KnowledgeTool
from ..tools.logistics_tool import LogisticsTool
from ..tools.order_tool import OrderTool
from ..tools.registry import ToolRegistry
from ..utils.logger import get_logger
from ..utils.version import build_run_fingerprint, new_run_id

logger = get_logger("services.agent_service")

# 运行终态计数（按 status / stop_reason 维度，补充到 /metrics）
RUN_TERMINAL_TOTAL = Counter(
    "agent_run_terminal_total",
    "Agent 运行终态计数",
    ["status", "stop_reason"],
)

# 在途业务数：容量观测口径（实际在途 vs 施加负载，见课程第 06 讲）
RUNS_INFLIGHT = Gauge(
    "agent_runs_inflight",
    "当前执行中的业务运行数（含流式）",
)

# 泵任务结束哨兵（内部使用）
_PUMP_DONE = object()


class AgentService:
    """客服工单 Agent 服务：持有运行器、执行器与版本指纹。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.prompt_manager = PromptManager(self.settings.prompts_dir)
        self.registry = ToolRegistry()
        self._java_client = None
        self._build_tools()
        self.executor = ToolExecutor(
            self.registry,
            tool_workers=self.settings.tool_workers,
            tool_queue_size=self.settings.tool_queue_size,
        )
        self.provider = self._build_provider()
        self.runner = HarnessRunner(
            self.provider, self.executor, self.settings, self.prompt_manager
        )
        self.exporter = self._build_exporter()
        self.fingerprint = build_run_fingerprint(
            Path(__file__).resolve().parents[2], self.settings
        )

    # ---- 组装 ----
    def _build_tools(self) -> None:
        # 数据从 SQLite 读取：若库不存在则自动从共享数据集导入
        from ..models.database import Database, load_seeds

        db = Database(
            self.settings.db_path,
            mysql=self.settings.mysql if self.settings.db_dialect == "mysql" else None,
        )
        if db.count_orders() == 0:
            orders, logistics, knowledge = load_seeds(self.settings.data_dir)
            db.seed_loads(orders, logistics, knowledge)
        self.db = db
        from ..api.catalog import Catalog

        self.catalog = Catalog(self.settings, db)

        # 订单/物流查询链路按 java_service 开关切换（Java 服务设计说明 §6）：
        # HTTP 版调 java-order-logistics 微服务，直读版查本进程快照库；
        # 两种口径的时延分布不可比，版本指纹已单列 java_service_enabled。
        if self.settings.java_service["enabled"]:
            from ..tools.java_service_tool import (
                JavaLogisticsTool,
                JavaOrderTool,
                JavaServiceClient,
            )

            self._java_client = JavaServiceClient(
                self.settings.java_service["base_url"]
            )
            self.registry.register(
                JavaOrderTool(
                    self._java_client, self.settings.tool_config("query_order")
                ),
                enabled=bool(self.settings.tool_config("query_order").get("enabled", True)),
            )
            self.registry.register(
                JavaLogisticsTool(
                    self._java_client, self.settings.tool_config("query_logistics")
                ),
                enabled=bool(
                    self.settings.tool_config("query_logistics").get("enabled", True)
                ),
            )
        else:
            self.registry.register(
                OrderTool(db, self.settings.tool_config("query_order")),
                enabled=bool(self.settings.tool_config("query_order").get("enabled", True)),
            )
            self.registry.register(
                LogisticsTool(db, self.settings.tool_config("query_logistics")),
                enabled=bool(
                    self.settings.tool_config("query_logistics").get("enabled", True)
                ),
            )
        self.registry.register(
            KnowledgeTool(db, self.settings.tool_config("search_knowledge")),
            enabled=bool(
                self.settings.tool_config("search_knowledge").get("enabled", True)
            ),
        )

    def _build_provider(self) -> BaseProvider:
        if self.settings.llm_provider == "openai":
            from ..llm.openai_provider import OpenAIProvider

            return OpenAIProvider(
                self.settings,
                self.prompt_manager,
                mode=self.settings.llm_tool_calling_mode,
            )
        return MockProvider(self.settings)

    def _build_exporter(self) -> LangfuseExporter | None:
        cfg = LangfuseConfig(
            enabled=self.settings.langfuse_enabled,
            host=self.settings.langfuse_host,
            public_key=self.settings.langfuse_public_key,
            secret_key=self.settings.langfuse_secret_key,
            timeout_s=self.settings.langfuse_timeout_s,
            stage="s01",
        )
        if not cfg.enabled:
            return None
        return LangfuseExporter(cfg)

    # ---- 异步入口（API 与异步调用方使用）----
    async def arun(self, run_input: RunInput):
        """非流式执行一笔业务，返回终态 RunResult。

        与 astream 消费同一运行流程：公共事件（含工具事件与 done 终态）
        一并交给 Langfuse 缓冲，内部模型调用事件经 internal_sink 上报。
        """
        result_dict: dict = {}
        sink = self.exporter.observe if self.exporter else None
        RUNS_INFLIGHT.inc()
        try:
            async for event in self.runner.run_stream(run_input, internal_sink=sink):
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
        """流式执行：逐个产出公共事件；done 事件承载完整终态。

        经泵任务桥接运行器事件：消费端断连（任务取消或生成器关闭）时
        取消泵任务 → 运行器记录取消终态并停止后续动作；此处补齐终态
        计数与 Langfuse 部分证据上报。泵结束后放入哨兵，保证 SSE 正常收尾。
        """
        queue: asyncio.Queue = asyncio.Queue()
        last_event: dict = {}
        sink = self.exporter.observe if self.exporter else None
        pump_error: list[BaseException] = []
        RUNS_INFLIGHT.inc()

        async def pump() -> None:
            try:
                async for event in self.runner.run_stream(run_input, internal_sink=sink):
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

        pump_task = asyncio.create_task(pump(), name="astream-pump")
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
            # 消费端关闭生成器（SSE 断连的常见路径）
            self._cancel_pump(pump_task)
            self._on_cancelled(run_input, last_event)
            raise
        except asyncio.CancelledError:
            self._cancel_pump(pump_task)
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
        run_id = last_event.get("run_id", "")
        status = "cancelled"
        stop_reason = "client_disconnect"
        RUN_TERMINAL_TOTAL.labels(status=status, stop_reason=stop_reason).inc()
        if self.exporter and run_id:
            # 部分证据上报：标注取消终态，不等待迟到结果
            self.exporter.observe({
                "run_id": run_id,
                "task_id": last_event.get("task_id", ""),
                "batch_id": last_event.get("batch_id", ""),
                "type": "done",
                "schema_version": last_event.get("schema_version", 2),
                "status": status,
                "stop_reason": stop_reason,
                "response": "",
                "metrics": {},
            })
            self.exporter.schedule_flush(
                run_id, query=run_input.query,
                conversation_id=run_input.conversation_id,
            )
        logger.warning(
            "SSE 消费端断连 run_id=%s task_id=%s",
            run_id, last_event.get("task_id", ""),
        )

    def _count_terminal(self, event: dict) -> None:
        RUN_TERMINAL_TOTAL.labels(
            status=str(event.get("status", "unknown")),
            stop_reason=str(event.get("stop_reason", "")),
        ).inc()

    # ---- 同步兼容入口（仅限无运行中事件循环的调用方）----
    def process(self, query: str) -> dict:
        """非流式处理一笔业务（同步包装）。异步调用方请使用 arun()。"""
        result = asyncio.run(self.arun(RunInput(query=query)))
        return {
            "schema_version": result.schema_version,
            "task_id": result.task_id,
            "run_id": result.run_id,
            "batch_id": result.batch_id,
            "status": result.status,
            "stop_reason": result.stop_reason,
            "response": result.response,
            "intent": result.intent,
            "error": result.error,
            "metrics": result.metrics,
        }

    def process_input(self, run_input: RunInput) -> dict:
        """带身份的同步执行（回放模式经线程池调用）。"""
        return asyncio.run(self.arun(run_input)).model_dump()

    # ---- 生命周期 ----
    async def aclose(self) -> None:
        """进程收尾：关闭 Provider 连接与工具工作池，等待观测上报完成。"""
        await self.provider.aclose()
        await self.executor.aclose()
        if self._java_client is not None:
            self._java_client.close()
        if self.exporter:
            await self.exporter.aclose()

    # ---- 元信息 ----
    @property
    def tools_description(self) -> str:
        return self.executor.describe()

    def versions(self) -> dict:
        """版本指纹 + 协议模式 + 有效预算 + 运行依赖信息。"""
        payload = dict(self.fingerprint)
        payload["protocol_mode"] = {
            "llm_provider": self.settings.llm_provider,
            "tool_calling_mode": self.settings.llm_tool_calling_mode,
            "stream_mode": self.settings.stream_mode,
        }
        payload["effective_limits"] = {
            "max_iterations": self.settings.max_iterations,
            "max_tool_calls": self.settings.max_tool_calls,
            "request_timeout_s": self.settings.request_timeout,
            "tool_workers": self.settings.tool_workers,
            "tool_queue_size": self.settings.tool_queue_size,
        }
        payload["observability"] = {"langfuse_enabled": bool(self.exporter)}
        return payload

    def new_batch_id(self) -> str:
        """生成批次 ID（batch_id），用于压测报告目录与 Trace 关联。"""
        return new_run_id(self.fingerprint.get("fingerprint"))


def dump_result(result) -> str:
    """结构化日志辅助：终态摘要（不含完整答复文本）。"""
    return json.dumps({
        "run_id": result.run_id,
        "task_id": result.task_id,
        "status": result.status,
        "stop_reason": result.stop_reason,
    }, ensure_ascii=False)
