"""超管引擎：Multi-Agent 编排的串行边界处。

执行流程（与 02-architecture + 03-agent-design 对齐）：
    超管规划（识别意图 → 分解 Agent 任务）
      → 并行扇出（专业子 Agent 独立决策并执行工具；共享 LLM 调用）
      → 结果聚合（tool_used 级聚合，兼容案例一判定口径）
      → 聚合校验（validation Agent 检查事实完整性）
      → 答复生成（超管汇总生成自然语言答复）

可靠性红线（对应 04-communication 的容错参数）：
- 单个子 Agent 超时：settings.agent_timeout_ms（默认 3000ms）
- 失败重试：max_retries 次，指数退避
- 熔断：连续 breaker_threshold 次失败 → 熔断 breaker_window_ms，
  熔断期间该子 Agent 直接降级（skipped），不拖垮整体业务
- 全流程总超时：settings.supervisor_timeout_ms（默认 8000ms），超时以降级答复兜底

数据访问：本引擎通过 Registry 调用工具，工具查询使用独立只读连接
（见 src/models/database.py 的每请求连接改造），高并发下无 SQLite 竞态。
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
import uuid
from dataclasses import dataclass, field

from ..llm.base import LLMResult, Message
from ..models.schemas import ToolCallInfo
from ..tools.registry import ToolRegistry
from ..utils.logger import get_logger
from .circuit_breaker import CircuitBreaker
from .worker import AgentCallInfo, Workforce, to_tool_used

logger = get_logger("supervisor")

FINAL_COMPLAINT = (
    "非常抱歉给您带来不好的体验，我已将您的问题记录并转交人工客服，"
    "请保持电话畅通，我们会尽快联系您处理。"
)
FINAL_CLARIFY = (
    "请问您方便提供订单号或物流单号吗？这样我能帮您准确查询进展。"
)
FINAL_NOT_FOUND = (
    "抱歉，暂时没有查询到相关信息。请确认订单号是否正确，"
    "或联系人工客服获取进一步帮助。"
)


@dataclass
class StepTiming:
    kind: str  # planning / fanout / tool / validation / generation
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
    # 多 Agent 特有的结构化输出
    agents: list[AgentCallInfo] = field(default_factory=list)
    plan_agents: list[str] = field(default_factory=list)
    fanout: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    degraded: bool = False
    degraded_agents: list[str] = field(default_factory=list)
    # 兼容案例一字段
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    steps: list[StepTiming] = field(default_factory=list)
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ttft_ms: float = 0.0
    tpots_ms: list[float] = field(default_factory=list)
    e2e_ms: float = 0.0
    plan_latency_ms: float = 0.0

    def metrics(self, with_trace: bool = True) -> dict:
        avg_tpot = (
            round(sum(self.tpots_ms) / len(self.tpots_ms), 3) if self.tpots_ms else 0.0
        )
        base = {
            "ttft_ms": round(self.ttft_ms, 3),
            "tpot_ms": avg_tpot,
            "e2e_ms": round(self.e2e_ms, 3),
            "token_count": self.completion_tokens or self.total_tokens,
            "total_tokens": self.total_tokens,
        }
        if with_trace:
            base["trace"] = {
                "intent": self.intent,
                "plan_agents": self.plan_agents,
                "plan_latency_ms": round(self.plan_latency_ms, 3),
                "agents": [a.to_dict() for a in self.agents],
                "fanout": {
                    k: (round(v, 3) if isinstance(v, float) else v)
                    for k, v in self.fanout.items()
                },
                "validation": dict(self.validation),
                "degraded": self.degraded,
                "degraded_agents": self.degraded_agents,
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


class SupervisorEngine:
    """Multi-Agent 超管执行引擎。"""

    def __init__(
        self,
        llm,
        workforce: Workforce,
        prompt_manager,
        settings,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.llm = llm
        self.workforce = workforce
        self.prompt_manager = prompt_manager
        self.settings = settings
        self.registry = registry or ToolRegistry()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._fault_hits: dict[str, int] = {}

    def _breaker(self, agent: str) -> CircuitBreaker:
        if agent not in self._breakers:
            self._breakers[agent] = CircuitBreaker(
                threshold=self.settings.breaker_threshold,
                window_ms=self.settings.breaker_window_ms,
            )
        return self._breakers[agent]

    def _new_ids(self) -> tuple[str, str]:
        run = uuid.uuid4().hex[:10]
        return f"task_{run}", f"run_{run}"

    ###########################################################################
    # 非流式执行
    ###########################################################################
    def execute(self, query: str) -> AgentResult:
        task_id, run_id = self._new_ids()
        result = AgentResult(task_id=task_id, run_id=run_id, query=query)
        t_start = time.perf_counter()
        default_response = FINAL_NOT_FOUND
        try:
            default_response = self._run_pipeline(query, result, on_event=None)
        finally:
            result.response = result.response or default_response
            result.e2e_ms = (time.perf_counter() - t_start) * 1000.0
        return result

    def _run_pipeline(self, query: str, result: AgentResult, on_event=None, stream_generation: bool = False) -> str:
        """编排主线：返回“未走 LLM 答复生成时直接定稿的答复”（空串则后续生成）。

        stream_generation=True 时跳过第 6 步，答复由 execute_stream 按 token 节奏
        真实流式生成（TTFT/TPOT 实测），避免重复调用导致 token 重复入账。
        """
        # 1) 超管规划
        plan = self._plan(query, result)
        result.plan_agents = list(plan.agents)
        if on_event:
            on_event("agent_plan", {"agents": plan.agents})

        if plan.intent == "complaint":
            return FINAL_COMPLAINT

        # 2) 并行扇出：仅调度有数据任务的子 Agent
        data_agents = [name for name in plan.agents if name != "validation_agent"]
        result.agents = []
        if data_agents:
            infos, fanout = self._fan_out(data_agents, query, result, on_event=on_event)
            result.agents = infos
            result.fanout = fanout

        tool_used = to_tool_used(result.agents)
        result.tool_calls = [
            ToolCallInfo(
                tool=a.tool,
                args=a.args,
                latency_ms=a.tool_ms,
                timed_out=a.status == "timeout",
                error=a.error,
                result=a.result,
            )
            for a in result.agents
            if a.tool
        ]

        # 3) 聚合校验：只要派发了数据 Agent 就必须执行。
        #    子 Agent 全部被熔断跳过时（无任何工具结果），校验 Agent 仍如实
        #    给出事实缺失清单并写入 trace，保证“校验 Agent 恒有执行记录”。
        if data_agents:
            self._validate(query, tool_used, result, on_event=on_event)
            if "validation_agent" not in result.plan_agents:
                result.plan_agents.append("validation_agent")

        # 4) 降级标注
        self._mark_degraded(result)

        # 5) 数据结果判定：无数据就定稿，有数据但全部未命中则如实致歉
        if not data_agents:
            # 未派发数据 Agent（澄清类意图）：主动问询
            return FINAL_CLARIFY
        if not tool_used:
            # 子 Agent 因缺参数未取到数据（no_tool）：视为澄清类，主动问询；
            # 否则视为全部失败：交给答复生成走降级致歉（降级关闭时直接致歉兜底）
            if all(a.status == "no_tool" for a in result.agents):
                return FINAL_CLARIFY
            return "" if self.settings.degradation_enabled else FINAL_NOT_FOUND
        if all(
            not (tool_used.get(key) or {}).get("found")
            for key in ("query_order", "query_logistics", "search_knowledge")
            if tool_used.get(key)
        ):
            return FINAL_NOT_FOUND

        # 6) 答复生成
        if stream_generation:
            return ""
        self._generation(query, tool_used, result, on_event=on_event)
        return ""

    def _plan(self, query: str, result: AgentResult):
        plan = self.llm.plan(query)
        intent = plan.content or "clarification"
        agents = list(plan.raw.get("agents") or [])
        result.intent = intent
        result.plan_latency_ms += plan.latency_ms
        self._account(result, plan)
        result.steps.append(
            StepTiming(kind="planning", name="supervisor_plan", latency_ms=plan.latency_ms)
        )
        return _Plan(intent=intent, agents=agents)

    def _fan_out(self, agents: list[str], query: str, result: AgentResult, on_event=None):
        """并行调度子 Agent；记录串行和/并行墙钟，供并行效率计算。"""
        limits = max(1, min(self.settings.parallel_limit, len(agents)))
        with cf.ThreadPoolExecutor(max_workers=limits) as pool:
            futures = {
                pool.submit(self._invoke_agent, name, query): name
                for name in agents
                if self.workforce.get(name) is not None
            }
            infos = [f.result() for f in cf.as_completed(futures)]

        for info in infos:
            result.agents.append(info)
            if info.tool:
                if on_event:
                    on_event("tool_call", {
                        "tool": info.tool,
                        "args": info.args,
                        "agent": info.name,
                        "status": info.status,
                        "latency_ms": round(info.tool_ms, 3),
                    })
                    on_event("tool_result", {
                        "tool": info.tool,
                        "agent": info.name,
                        "status": info.status,
                        "result": info.result or {},
                    })

        serial_sum = sum((a.total_ms or 0.0) for a in infos)
        round_wall = max((a.total_ms or 0.0) for a in infos) if infos else 0.0
        fanout = {
            "round_wall_ms": round(round_wall, 3),
            "serial_sum_ms": round(serial_sum, 3),
            "efficiency": round(serial_sum / round_wall, 4) if round_wall else 0.0,
            "agents": len(infos),
        }
        result.steps.append(
            StepTiming(kind="fanout", name="parallel_dispatch", latency_ms=round_wall)
        )
        return infos, fanout

    def _invoke_agent(self, agent: str, query: str) -> AgentCallInfo:
        """单子 Agent 调度：熔断检查 → 故障注入 → 重试（指数退避）。"""
        breaker = self._breaker(agent)
        if breaker.is_open:
            remaining = breaker.state()["remaining_ms"]
            return AgentCallInfo(
                name=agent,
                status="skipped_breaker",
                error=f"circuit open, remaining {remaining:.0f}ms",
            )

        attempts = 0
        last_err = None
        last_status = "error"
        while attempts < self.settings.max_retries + 1:
            attempts += 1
            info = None
            try:
                self._maybe_inject_fault(agent)
                info = self.workforce.get(agent).run(query)
            except _FaultTimeout as exc:
                info = AgentCallInfo(name=agent, status="timeout", error=str(exc))
            except Exception as exc:  # noqa: BLE001
                info = AgentCallInfo(name=agent, status="error", error=str(exc))

            info.attempts = attempts
            if info.status == "success":
                breaker.record_success()
                return info
            # 成功但降级无需重试：no_tool 属于正常业务结果（缺参数）
            if info.status == "no_tool":
                breaker.record_success()
                return info

            last_err = info.error
            last_status = info.status
            if attempts <= self.settings.max_retries:
                backoff = self.settings.retry_backoff_ms * (2 ** (attempts - 1))
                time.sleep(backoff / 1000.0)

        breaker.record_failure()
        opened = breaker.state()["open"]
        return AgentCallInfo(
            name=agent,
            status="skipped_breaker" if opened else last_status,
            error=last_err,
            attempts=attempts,
        )

    def _maybe_inject_fault(self, agent: str) -> None:
        """按配置注入故障：支持按 Agent 或共享 LLM 注入 timeout/error/down。"""
        s = self.settings
        if not s.fault_enabled:
            return
        target_hit = s.fault_target in ("", agent) or (s.fault_target == "llm" and agent != "validation_agent")
        if not target_hit:
            return
        counter = self._fault_hits.get(agent, 0)
        if s.fault_limit and counter >= s.fault_limit:
            return
        self._fault_hits[agent] = counter + 1
        if s.fault_mode == "timeout":
            time.sleep(s.fault_latency_ms / 1000.0)
            raise _FaultTimeout(f"处理超时（注入耗时 {s.fault_latency_ms:.0f}ms）")
        if s.fault_mode == "error":
            raise RuntimeError("子服务返回 5xx（注入）")
        if s.fault_mode == "down":
            raise ConnectionError("子服务不可用（注入）")

    def _validate(self, query: str, tool_used: dict, result: AgentResult, on_event=None) -> None:
        facts = {
            "order": tool_used.get("query_order"),
            "logistics": tool_used.get("query_logistics"),
            "knowledge": tool_used.get("search_knowledge"),
        }
        v = self.llm.validation(query, facts)
        result.validation = {
            "status": v.content or "complete",
            "missing": list(v.raw.get("missing") or []),
            "latency_ms": round(v.latency_ms, 3),
            "tokens": v.total_tokens,
        }
        self._account(result, v)
        result.steps.append(
            StepTiming(kind="validation", name="validation_agent", latency_ms=v.latency_ms)
        )
        if on_event:
            on_event("validation", result.validation)

    def _mark_degraded(self, result: AgentResult) -> None:
        bad = [
            a.name
            for a in result.agents
            if a.status in ("timeout", "error", "skipped_breaker")
        ]
        result.degraded_agents = sorted(set(bad))
        result.degraded = bool(bad) and self.settings.degradation_enabled

    def _generation(self, query: str, tool_used: dict, result: AgentResult, on_event=None) -> None:
        facts = {
            "order": tool_used.get("query_order"),
            "logistics": tool_used.get("query_logistics"),
            "knowledge": tool_used.get("search_knowledge"),
        }
        system = self.prompt_manager.system_prompt(self.workforce.describe_roles())
        gen_prompt = self.prompt_manager.response_gen(
            query,
            json.dumps(facts.get("order") or {}, ensure_ascii=False),
            json.dumps(facts.get("logistics") or {}, ensure_ascii=False),
        )
        degraded = {"agents": result.degraded_agents} if result.degraded else {}
        messages = [
            Message(role="system", content=system),
            Message(
                role="tool",
                content=json.dumps({**facts, "degraded": degraded}, ensure_ascii=False),
            ),
            Message(role="user", content=gen_prompt),
        ]
        answer = self._llm_call(messages)
        self._account(result, answer)
        result.plan_latency_ms += answer.ttft_ms
        result.steps.append(
            StepTiming(kind="generation", name="response_gen", latency_ms=answer.latency_ms)
        )
        content = answer.content or FINAL_NOT_FOUND
        result.response = content
        # 合成口径（非流式）：TTFT = 规划 + 扇出墙钟 + 校验 + 生成首 token
        ttft = (
            result.plan_latency_ms - answer.ttft_ms
            + result.fanout.get("round_wall_ms", 0.0)
            + result.validation.get("latency_ms", 0.0)
            + answer.ttft_ms
        )
        result.ttft_ms = max(ttft, 0.0)
        token_gap = self.settings.mock_token_latency_ms
        n_tokens = max(1, result.completion_tokens or len(content))
        result.tpots_ms = [token_gap] * n_tokens

    def _llm_call(self, messages: list[Message]) -> LLMResult:
        try:
            return self.llm.complete(messages)
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM 调用失败")
            raise RuntimeError(f"LLM 调用失败: {exc}") from exc

    def _account(self, result: AgentResult, llm_result: LLMResult) -> None:
        result.total_tokens += llm_result.total_tokens
        result.prompt_tokens += llm_result.prompt_tokens
        result.completion_tokens += llm_result.completion_tokens

    ###########################################################################
    # 流式执行（真实 SSE）
    ###########################################################################
    def execute_stream(self, query: str, run_id: str | None = None):
        task_id, rid = self._new_ids()
        if run_id:
            rid = run_id
        result = AgentResult(task_id=task_id, run_id=rid, query=query)
        t0 = time.perf_counter()
        tool_events: list[dict] = []

        def ts_ms() -> float:
            return round((time.perf_counter() - t0) * 1000.0, 3)

        def on_event(kind: str, payload: dict) -> None:
            tool_events.append({"type": kind, "ts_ms": ts_ms(), **payload})

        tpots: list[float] = []
        state = {"t_prev": None, "tokens": 0}
        default_response = FINAL_NOT_FOUND

        def emit(ch: str) -> dict:
            now = time.perf_counter()
            if state["t_prev"] is None:
                result.ttft_ms = (now - t0) * 1000.0
            else:
                tpots.append((now - state["t_prev"]) * 1000.0)
            state["t_prev"] = now
            state["tokens"] += 1
            result.response += ch
            return {
                "type": "answer",
                "content": ch,
                "ts_ms": round((now - t0) * 1000.0, 3),
            }

        yield {"type": "thinking", "content": "超管正在理解问题并规划子 Agent 任务...", "ts_ms": ts_ms()}

        finalized = ""
        try:
            finalized = self._run_pipeline(query, result, on_event=on_event, stream_generation=True)
            if finalized:
                result.response = finalized
            for ev in tool_events:
                yield ev

            if result.response:
                # 无 LLM 生成的直接定稿答复：按字符流出
                final_text, result.response = result.response, ""
                for ch in final_text:
                    yield emit(ch)
            else:
                # 正常生成：走真实流式（首 token 消耗生成延迟）
                tool_used = to_tool_used(result.agents)
                facts = {
                    "order": tool_used.get("query_order"),
                    "logistics": tool_used.get("query_logistics"),
                    "knowledge": tool_used.get("search_knowledge"),
                }
                system = self.prompt_manager.system_prompt(self.workforce.describe_roles())
                gen_prompt = self.prompt_manager.response_gen(
                    query,
                    json.dumps(facts.get("order") or {}, ensure_ascii=False),
                    json.dumps(facts.get("logistics") or {}, ensure_ascii=False),
                )
                messages = [
                    Message(role="system", content=system),
                    Message(
                        role="tool",
                        content=json.dumps(
                            {**facts, "degraded": {"agents": result.degraded_agents} if result.degraded else {}},
                            ensure_ascii=False,
                        ),
                    ),
                    Message(role="user", content=gen_prompt),
                ]
                answer_prompt = max(1, sum(len(m.content) for m in messages) // 2)
                result.prompt_tokens += answer_prompt
                result.total_tokens += answer_prompt
                for ch in self.llm.stream_sync(messages):
                    yield emit(ch)
                result.completion_tokens = max(result.completion_tokens, state["tokens"])
                result.total_tokens += result.completion_tokens
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式执行异常")
            if not result.response:
                for ch in FINAL_NOT_FOUND:
                    yield emit(ch)

        result.tpots_ms = tpots
        result.e2e_ms = (time.perf_counter() - t0) * 1000.0

        yield {
            "type": "done",
            "ts_ms": ts_ms(),
            "task_id": task_id,
            "run_id": rid,
            "intent": result.intent or "",
            "total_tokens": result.total_tokens,
            "metrics": result.metrics(with_trace=True),
        }


@dataclass
class _Plan:
    intent: str
    agents: list[str]


class _FaultTimeout(TimeoutError):
    pass