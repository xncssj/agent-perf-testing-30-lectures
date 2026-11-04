"""Workflow 图节点实现（Workflow 图设计说明 的节点清单，v2）。

节点类型：
- intent_recognition  意图识别（LLM 调用 1）
- data_retrieval      业务数据检索（工具扇出，订单/物流；经共享执行器）
- knowledge_retrieval 知识库检索（RAG，记录溯源）
- response_generation 答复生成（LLM 调用 2，记录生成引用）
- confidence_check    置信度/护栏/分支判定（规则，非 LLM）
- clarification       澄清（LLM 调用，至多总 2 次）

v2 Harness 挂钩（共享 Harness 设计说明 第 8.2 节）：节点通过上下文中的 _scope
登记模型用量与工具事件；data_retrieval 的工具尝试由根预算原子分配并经
共享有界执行器执行；人工转接分支只表述建议行为，不声称已转交。
"""
from __future__ import annotations

import time

from prometheus_client import Counter

from ..llm.base import LLMResult
from ..rag.engine import RagEngine
from ..tools.registry import ToolRegistry
from ..workflow.engine import NodeDef
from ..utils.logger import get_logger

logger = get_logger("workflow.nodes")

# 答复引用产出计数：仅在有检索引用时 inc（数据类请求无检索、无样本，口径不与请求总数混淆）
RAG_CITATIONS_TOTAL = Counter(
    "workflow_rag_citations_total",
    "答复引用产出条数（取检索命中 doc_id 前 2 条）",
)

# 意图 → 分支路由（互斥条件）
DATA_INTENTS = {"order_info", "logistics_query", "delivery_time", "refund_status",
                "complaint", "address_change", "payment_issue"}
KNOWLEDGE_INTENTS = {"return_policy", "exchange_policy", "after_sale", "contact_info",
                     "refund_policy"}
CLARIFY_INTENT = "clarification"

# 意图 → 工具序列（数据检索节点的扇出清单）
INTENT_TOOLS = {
    "order_info": ["query_order"],
    "refund_status": ["query_order", "query_logistics"],
    "payment_issue": ["query_order"],
    "address_change": ["query_order"],
    "delivery_time": ["query_order", "query_logistics"],
    "logistics_query": ["query_order", "query_logistics"],
    "complaint": ["query_order"],
}

ORDER_ID_RE = __import__("re").compile(r"(?<!\d)(\d{13})(?!\d)")
TRACKING_NO_RE = __import__("re").compile(r"(?P<carrier>SF|YT|ZTO|JD|EMS)\d{6,}")

# 人工处理建议：只表述建议行为，不声称已转交（与阶段一/二同口径）
HUMAN_SUGGEST_TEXT = (
    "建议您联系人工客服进一步处理，我们会重视您反馈的问题。\n"
)


class WorkflowNodes:
    """为执行器提供节点运行函数（输出以 dict 合并进执行上下文）。"""

    def __init__(self, llm, tools: ToolRegistry, rag: RagEngine, settings) -> None:
        self.llm = llm
        self.tools = tools
        self.rag = rag
        self.settings = settings
        # v2：共享有界工具执行器（由服务层注入；None 时回退 v1 fanout）
        self.tool_executor = None

    def runner(self) -> object:
        dispatch = {
            "intent_recognition": self._intent,
            "data_retrieval": self._data,
            "knowledge_retrieval": self._knowledge,
            "response_generation": self._generate,
            "confidence_check": self._confirm,
            "clarification": self._clarify,
        }

        def run(node: NodeDef, context: dict) -> dict:
            fn = dispatch.get(node.type)
            if fn is None:
                raise ValueError(f"未知节点类型: {node.type}")
            return fn(node, context)

        return run

    ###########################################################################
    def _intent(self, node: NodeDef, context: dict) -> dict:
        scope = context.get("_scope") or {}
        query = context["query"]
        res: LLMResult = self.llm.recognize_intent(query)
        _observe_context(res, self.settings)
        if scope.get("recorder") is not None:
            scope["recorder"].record_model_usage(
                "intent_recognition", getattr(self.llm, "model", ""),
                res.latency_ms, _usage_of(res),
            )
        # raw 键容错：真实模型只保证 intent/confidence（openai_llm），
        # mock 才四键齐全；要素缺失时按正则从 query 抽取（与 _data 同一套）
        raw = res.raw or {}
        return {
            "intent": raw.get("intent", "clarification"),
            "confidence": float(raw.get("confidence", 0.98)),
            "has_order": bool(raw.get("has_order", ORDER_ID_RE.search(query))),
            "has_tracking": bool(raw.get("has_tracking", TRACKING_NO_RE.search(query))),
            "node_llm_ms": res.latency_ms,
            "total_tokens": res.total_tokens,
        }

    def _data(self, node: NodeDef, context: dict) -> dict:
        query = context["query"]
        intent = context.get("intent", "")
        order_m = ORDER_ID_RE.search(query)
        track_m = TRACKING_NO_RE.search(query)
        order_id = order_m.group(0) if order_m else None
        tracking_no = track_m.group(0) if track_m else None

        tool_names = list(INTENT_TOOLS.get(intent, ["query_order", "query_logistics"]))
        calls: list[dict] = []
        if "query_order" in tool_names:
            calls.append({"name": "query_order", "order_id": order_id} if order_id else {"name": "query_order", "order_id": ""})
        if "query_logistics" in tool_names:
            if tracking_no:
                calls.append({"name": "query_logistics", "tracking_no": tracking_no})
            elif order_id:
                calls.append({"name": "query_logistics", "order_id": order_id})

        results = self._run_tools(calls, context)
        order_out = {"found": False}
        logistics_out = {"found": False}
        degraded = {}
        tool_calls = []
        for r in results:
            tool_calls.append({
                "tool": r["tool"], "params": r["params"], "found": r["found"],
                "latency_ms": r["latency_ms"], "error": r["error"],
            })
            if r["error"]:
                degraded.setdefault("reasons", []).append((r["tool"], r["error"]))
            if r["tool"] == "query_order" and r["found"]:
                order_out = {"found": True, "order": r["result"]["order"]}
                if not order_id:
                    order_id = r["result"]["order"].get("order_id")
            if r["tool"] == "query_logistics" and r["found"]:
                logistics_out = {"found": True, "logistics": r["result"]["logistics"]}
        # 订单缺物流时不降级，仅当工具级错误/超时才算降级
        if degraded.get("reasons"):
            degraded["marked"] = True
        return {
            "order": order_out,
            "logistics": logistics_out,
            "degraded": degraded,
            "tool_calls": tool_calls,
            "data_retrieval_ms": round(sum(r.get("latency_ms", 0) for r in tool_calls), 3),
        }

    def _run_tools(self, calls: list[dict], context: dict) -> list[dict]:
        """v2：工具调用经共享有界执行器（预算原子分配 + 事件），无执行器回退 v1。"""
        scope = context.get("_scope") or {}
        executor = scope.get("tool_executor")
        if executor is None or scope.get("ctx") is None:
            return self.tools.fanout(calls)
        results = []
        for call in calls:
            name = call["name"]
            kwargs = {k: v for k, v in call.items() if k != "name"}
            from harness.contracts import ToolCall

            obs = executor.run_sync(
                ToolCall(name=name, arguments=dict(kwargs)),
                scope["ctx"], scope["budget"], scope["recorder"],
            )
            results.append({
                "tool": name,
                "params": kwargs,
                "found": bool((obs.data or {}).get("found")),
                "result": obs.data or {},
                "latency_ms": obs.duration_ms,
                "error": obs.error,
            })
        return results

    def _knowledge(self, node: NodeDef, context: dict) -> dict:
        t0 = time.perf_counter()
        retrieval = self.rag.search(context["query"], top_k=self.settings.top_k)
        return {
            "retrieval": retrieval,
            "retrieval_hits": retrieval["hits"],
            "rag_latency_ms": (time.perf_counter() - t0) * 1000.0,
        }

    def _generate(self, node: NodeDef, context: dict) -> dict:
        scope = context.get("_scope") or {}
        facts = {
            "order": context.get("order", {}),
            "logistics": context.get("logistics", {}),
            "degraded": context.get("degraded", {}),
        }
        hits = context.get("retrieval_hits") or []
        res = self.llm.generate_answer(context["query"], facts, hits)
        _observe_context(res, self.settings)
        if scope.get("recorder") is not None:
            scope["recorder"].record_model_usage(
                "response_generation", getattr(self.llm, "model", ""),
                res.latency_ms, _usage_of(res),
            )
        prev = context.get("total_tokens", 0)
        # 生成引用（共享 Harness 设计说明 第 8.2 节）：答复依据的检索文档 ID
        citations = [h.get("doc_id") for h in (context.get("retrieval") or {}).get("hits", [])][:2]
        if citations:
            RAG_CITATIONS_TOTAL.inc(len(citations))
        return {
            "response": res.content,
            "answer_tokens": res.total_tokens,
            "total_tokens": prev + res.total_tokens,
            "generate_llm_ms": res.latency_ms,
            "citations": citations,
        }

    def _confirm(self, node: NodeDef, context: dict) -> dict:
        confidence = context.get("confidence", 0.0)
        intent = context.get("intent", "")
        threshold = float(node.config.get("threshold", 0.8))
        human = confidence < threshold or intent in ("complaint",)
        branch = "human_transfer" if human else "auto_reply"

        from ..workflow.guardrail import guardrail

        response = context.get("response", "")
        guard = guardrail.check(response)
        if branch == "human_transfer" and not response.startswith(HUMAN_SUGGEST_TEXT):
            # 只表述建议行为，不声称已转交/已升级（与阶段一、二同口径）
            response = HUMAN_SUGGEST_TEXT + response
        return {
            "branch": branch,
            "confidence": confidence,
            "response": response,
            "guardrail": guard,
        }

    def _clarify(self, node: NodeDef, context: dict) -> dict:
        scope = context.get("_scope") or {}
        res = self.llm.clarify(context["query"])
        _observe_context(res, self.settings)
        if scope.get("recorder") is not None:
            scope["recorder"].record_model_usage(
                "clarification", getattr(self.llm, "model", ""),
                res.latency_ms, _usage_of(res),
            )
        prev = context.get("total_tokens", 0)
        return {
            "response": res.content,
            "branch": "clarification",
            "answer_tokens": res.total_tokens,
            "total_tokens": prev + res.total_tokens,
            "clarify_llm_ms": res.latency_ms,
        }


def _observe_context(res: LLMResult, settings) -> None:
    """模型调用后观测上下文窗口占用（用量口径：Mock=估算，OpenAI=上报）。"""
    from harness.metrics import observe_context_occupancy

    observe_context_occupancy(
        int(getattr(res, "prompt_tokens", 0) or 0),
        int(getattr(settings, "context_window_tokens", 0)),
    )


def _usage_of(res: LLMResult):
    """LLMResult 用量 -> 共享 ModelUsage（Mock 为估算，OpenAI 为上报）。"""
    from harness.contracts import ModelUsage

    source = "estimated"
    raw = getattr(res, "raw", {}) or {}
    if raw.get("usage_reported"):
        source = "reported"
    return ModelUsage(
        prompt_tokens=res.prompt_tokens or None,
        completion_tokens=res.completion_tokens or None,
        total_tokens=res.total_tokens or None,
        source=source,
    )
