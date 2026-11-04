"""Workflow 执行器：固定 DAG 的节点调度、互斥分支、并行汇合、循环预算与检查点。

设计对齐 Workflow 图设计说明：
- 互斥分支：条件出边按声明顺序评估取首个满足者，无条件出边为 default 兜底（至多一条）
- 并行分支：出边全部标记 parallel 时整组并行（asyncio.gather），完成后进入 join
- 循环预算：单节点执行次数超过 max_node_executions 即熔断
- 检查点：节点成功即存档，故障后从最近检查点续跑（已完成节点不重复执行）
- 故障注入：按 target/node 选择节点，mode=error/timeout/down，limit 自愈
"""
from __future__ import annotations

import sys

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.budget import BudgetExceeded, DeadlineExceeded  # noqa: E402

from ..utils.logger import get_logger  # noqa: E402
from .checkpoint_store import (  # noqa: E402
    MemoryCheckpointStore,
    build_checkpoint_store,
)


logger = get_logger("workflow.engine")


class NodeError(Exception):
    """节点执行失败。"""


class LoopBudgetExceeded(NodeError):
    """节点循环预算耗尽（max_node_executions）——映射 budget_exhausted 终态。"""


@dataclass
class Edge:
    source: str
    target: str
    condition: str | None = None
    parallel: bool = False


@dataclass
class NodeDef:
    id: str
    type: str
    name: str = ""
    config: dict = field(default_factory=dict)


@dataclass
class NodeExecutionRec:
    node_id: str
    node_type: str
    status: str = "completed"  # completed / failed / skipped
    latency_ms: float = 0.0
    error: str | None = None
    visits: int = 1


@dataclass
class Checkpoint:
    execution_id: str
    completed_node_id: str
    context_snapshot: dict
    created_at: str


class CheckpointStore(MemoryCheckpointStore):
    """内存检查点存储（兼容别名；持久化实现见 checkpoint_store.py）。"""


class WorkflowExecutor:
    """固定 DAG 执行器（单执行线程内串行/并行调度）。"""

    def __init__(
        self,
        nodes: list[NodeDef],
        edges: list[Edge],
        start_node: str,
        node_runner: Callable[[NodeDef, dict], dict],
        settings,
    ) -> None:
        self._nodes = {n.id: n for n in nodes}
        self._edges = edges
        self.start_node = start_node
        self._node_runner = node_runner
        self.settings = settings
        # 检查点存储：memory（默认，进程重启即丢）/ sqlite（跨重启续跑）
        self.checkpoints = build_checkpoint_store(settings)
        # 生命周期：距上次 GC 的存档计数（节流，避免每次存档都回收）
        self._saves_since_gc = 0
        # 故障注入状态（跨 execute 持续，自愈用）
        self.fault_failures = 0
        self._validate()

    def _validate(self) -> None:
        if self.start_node not in self._nodes:
            raise ValueError(f"start_node 不存在: {self.start_node}")
        outstanding = {self.start_node}
        for e in self._edges:
            if e.source not in self._nodes or e.target not in self._nodes:
                raise ValueError(f"边引用不存在的节点: {e.source} -> {e.target}")
            if e.parallel and e.condition:
                raise ValueError(f"并行边不得携带条件: {e.source} -> {e.target}")
        # 互斥分支限制：无条件出边至多一条
        from collections import Counter

        for src in set(e.source for e in self._edges):
            if sum(1 for e in self._edges if e.source == src and not e.condition and not e.parallel) > 1:
                raise ValueError(f"节点 {src} 存在多条无条件出边（default 只允许一条）")

    ###########################################################################
    # 公共入口
    ###########################################################################
    def execute(
        self,
        query: str,
        execution_id: str | None = None,
        start_node: str | None = None,
        context_seed: dict | None = None,
        scope: dict | None = None,
    ) -> dict:
        """执行 Workflow，返回结构化结果（path / branch / response / facts / tokens）。

        v2：scope 为 Harness 运行作用域（recorder/ctx/budget/check_deadline/
        tool_executor），注入节点上下文后即在节点执行边界生效；检查点续跑后
        重新注入（快照不携带 scope）。
        """
        context: dict = {"query": query, **({} if context_seed is None else context_seed)}
        context.setdefault("node_visit_counts", {})
        if scope is not None:
            context["_scope"] = scope

        resume_from: str | None = None
        if (
            execution_id
            and self.settings.checkpoint_enabled
            and context_seed is None
        ):
            cp = self.checkpoints.load_latest(execution_id)
            if cp is not None:
                context = dict(cp.context_snapshot)
                context.setdefault("node_visit_counts", {})
                resume_from = cp.completed_node_id
        if scope is not None:
            context["_scope"] = scope

        start = start_node or self.start_node
        t0 = time.perf_counter()
        try:
            if resume_from is not None:
                self._continue_from(resume_from, context, execution_id)
            else:
                self._traverse(start, context, execution_id)
            status = "completed"
            error = None
        except BudgetExceeded as exc:
            status, error = "budget_exhausted", str(exc)
        except DeadlineExceeded:
            status, error = "timed_out", "request_deadline"
        except LoopBudgetExceeded as exc:
            status, error = "budget_exhausted", str(exc)
        except NodeError as exc:
            status = "failed"
            error = str(exc)
            # 故障结束前保存最近检查点（已完成节点），便于续跑
            if self.settings.checkpoint_enabled and execution_id:
                last = context.get("path") or []
                self._save_checkpoint(execution_id, context, last[-1] if last else None)
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"

        # 生命周期（课程正文对应段落）：检查点只为未到成功终态的运行保留——
        # completed 后不再有续跑需求，按策略即时清理；失败/超时/预算终态保留，
        # 供同 execution_id 续跑。purge 策略进入运行指纹（影响可恢复语义）。
        if (
            status == "completed"
            and execution_id
            and self.settings.checkpoint_enabled
            and getattr(self.settings, "checkpoint_purge_on_completed", True)
        ):
            self.checkpoints.delete(execution_id)

        return self._build_result(query, context, status, error, time.perf_counter() - t0)

    async def execute_async(
        self, query: str, execution_id: str | None = None
    ) -> dict:
        """异步入口：避免阻塞事件循环（HTTP 层使用）。"""
        return await asyncio.to_thread(self.execute, query, execution_id)

    ###########################################################################
    # 内部调度
    ###########################################################################
    def _traverse(self, node_id: str, context: dict, execution_id: str | None) -> None:
        self._execute_node(node_id, context, execution_id)
        node = self._nodes[node_id]
        self._execute_next_nodes(node, context, execution_id)

    def _continue_from(self, completed_node_id: str, context: dict, execution_id: str | None) -> None:
        """从检查点续跑：已完成节点不重复执行，直接评估其出边。"""
        node = self._nodes[completed_node_id]
        self._execute_next_nodes(node, context, execution_id)

    def _execute_node(self, node_id: str, context: dict, execution_id: str | None) -> None:
        node = self._nodes[node_id]
        counts = context.setdefault("node_visit_counts", {})
        counts[node_id] = counts.get(node_id, 0) + 1
        if counts[node_id] > self.settings.max_node_executions:
            raise LoopBudgetExceeded(
                f"节点 {node_id} 超出循环预算 {self.settings.max_node_executions}"
            )

        # v2 Harness 挂钩（共享 Harness 设计说明 第 8.2 节）：节点执行边界接入
        # 身份、deadline 与事件（事件携带 node_id）；scope 由服务层注入。
        scope = context.get("_scope")

        # 故障注入钩子：位于节点重试循环内，单次瞬态失败可被重试吸收，
        # 达到 limit 后停止注入即“自愈”（limit>0 场景）。
        n0 = time.perf_counter()
        attempts = 0
        while True:
            try:
                if scope is not None:
                    scope["check_deadline"]()
                self._maybe_inject(node, context)
                output = self._node_runner(node, context)
                break
            except NodeError:
                attempts += 1
                if attempts > self.settings.max_retries:
                    context.setdefault("node_exec_errors", []).append(
                        {"node": node_id, "error": f"重试 {self.settings.max_retries} 次后仍失败"}
                    )
                    raise
                time.sleep(self.settings.retry_backoff_ms / 1000.0)

        latency = (time.perf_counter() - n0) * 1000.0
        if scope is not None:
            scope["record_node"](node_id, node.type, latency, "completed", attempts)
        context.setdefault("node_executions", []).append({
            "node_id": node_id,
            "node_type": node.type,
            "latency_ms": round(latency, 3),
            "status": "completed",
            "attempts": attempts,
        })
        context["node_executions"][-1].update(output if isinstance(output, dict) else {"output": output})
        context.update(output if isinstance(output, dict) else {})
        context.setdefault("path", []).append(node_id)
        # 检查点：节点合并输出后立即存档
        if self.settings.checkpoint_enabled and execution_id:
            self._save_checkpoint(execution_id, context, node_id)

    def _save_checkpoint(self, execution_id: str, context: dict, node_id: str | None) -> None:
        cp = Checkpoint(
            execution_id=execution_id,
            completed_node_id=node_id or context.get("path", [])[-1],
            context_snapshot=context.copy(),
            created_at=datetime.now().isoformat(),
        )
        self.checkpoints.save(cp)
        # 生命周期：每 N 次存档节流触发一次 GC，回收失败后遗留的过期/超量
        # 检查点（如故障后从未续跑的执行）；GC 失败不影响主流程。
        self._saves_since_gc += 1
        every = int(getattr(self.settings, "checkpoint_gc_every_saves", 64) or 0)
        if every > 0 and self._saves_since_gc >= every:
            self._gc_checkpoints()

    def _gc_checkpoints(self) -> int:
        """按配置的存活时长与总量上限回收检查点，返回回收条数。"""
        self._saves_since_gc = 0
        max_age_s = getattr(self.settings, "checkpoint_gc_max_age_s", 0)
        max_count = getattr(self.settings, "checkpoint_gc_max_count", 0)
        try:
            return self.checkpoints.gc(
                max_age_s=float(max_age_s) if max_age_s else None,
                max_count=int(max_count) if max_count else None,
            )
        except Exception as exc:  # noqa: BLE001  回收失败不阻断业务运行
            logger.warning("检查点 GC 失败: %s", exc)
            return 0

    def _execute_next_nodes(self, node: NodeDef, context: dict, execution_id: str | None) -> None:
        next_edges = [e for e in self._edges if e.source == node.id]
        if not next_edges:
            return

        # 并行分支：全部标记 parallel -> gather + join
        if next_edges and all(e.parallel for e in next_edges):
            for e in next_edges:
                self._check_loop_budget(e.target, context)
            join_id = self._find_join(node.id)
            for e in next_edges:
                self._traverse(e.target, context, execution_id)
            if join_id is not None:
                self._execute_node(join_id, context, execution_id)
            return

        # 互斥分支
        defaults = [e for e in next_edges if not e.condition and not e.parallel]
        chosen = None
        for e in next_edges:
            if e.condition and self._eval_condition(e.condition, context):
                chosen = e
                break
        if chosen is None and defaults:
            chosen = defaults[0]
        if chosen is not None:
            self._check_loop_budget(chosen.target, context)
            self._traverse(chosen.target, context, execution_id)

    def _check_loop_budget(self, target: str, context: dict) -> None:
        counts = context.get("node_visit_counts", {})
        if counts.get(target, 0) + 1 > self.settings.max_node_executions:
            raise LoopBudgetExceeded(
                f"节点 {target} 超出循环预算 {self.settings.max_node_executions}"
            )

    def _find_join(self, fork_node_id: str) -> str | None:
        for e in self._edges:
            if e.source == fork_node_id and self._nodes[e.target].type == "join":
                return e.target
        return None

    def _eval_condition(self, condition: str, context: dict) -> bool:
        try:
            return bool(eval(condition, {"__builtins__": {}}, context))
        except Exception:  # noqa: BLE001
            return False

    ###########################################################################
    # 故障注入
    ###########################################################################
    def _maybe_inject(self, node: NodeDef, context: dict) -> None:
        s = self.settings
        if not s.fault_enabled:
            return
        if s.fault_node_id and node.id != s.fault_node_id:
            return
        if s.fault_target and node.type != s.fault_target and node.name != s.fault_target:
            return
        if s.fault_limit and self.fault_failures >= s.fault_limit:
            return  # 已自愈，停止注入
        self.fault_failures += 1
        mode = s.fault_mode or "error"
        if mode == "timeout":
            time.sleep(s.fault_latency_ms / 1000.0)
            raise NodeError(f"节点 {node.id} 超时（故障注入）")
        if mode == "down":
            raise NodeError(f"节点 {node.id} 服务不可用（故障注入）")
        raise NodeError(f"节点 {node.id} 执行错误（故障注入）")

    ###########################################################################
    # 结果组装
    ###########################################################################
    def _build_result(self, query: str, context: dict, status: str, error: str | None, elapsed_s: float) -> dict:
        path = list(context.get("path", []))
        branch = context.get("branch", "")
        response = context.get("response", "")
        facts = {
            "order": context.get("order", {}),
            "logistics": context.get("logistics", {}),
            "degraded": context.get("degraded", {}),
        }
        retrieval = context.get("retrieval", {})
        tokens = context.get("total_tokens", 0)
        node_executions = list(context.get("node_executions", []))
        tool_calls = list(context.get("tool_calls", []))
        return {
            "query": query,
            "status": status,
            "error": error,
            "path": path,
            "branch": branch,
            "response": response,
            "facts": facts,
            "retrieval": retrieval,
            "citations": list(context.get("citations", [])),
            "total_tokens": tokens,
            "node_executions": node_executions,
            "tool_calls": tool_calls,
            "intent": context.get("intent", ""),
            "confidence": context.get("confidence", 0.0),
            "guardrail": context.get("guardrail", {}),
            "e2e_ms": round(elapsed_s * 1000.0, 3),
            "trace": {
                "path": path,
                "branch": branch,
                "intent": context.get("intent", ""),
                "degraded": bool(context.get("degraded")),
                "node_exec_errors": context.get("node_exec_errors", []),
            },
        }
