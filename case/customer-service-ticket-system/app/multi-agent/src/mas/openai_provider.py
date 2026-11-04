"""OpenAIProvider：OpenAI 兼容端点模型接入（Multi-Agent v2）。

与 v1 OpenAILlm 的语义决策协议保持一致（JSON 输出 + 规则回退）：
- plan：{"intent":"...","agents":[...]}，不规范时按规则回退规划；
- agent_task：{"tool":"...","args":{}}，缺参数输出 {"tool":null} 需澄清，
  语义不规范时按 Agent 负责工具回退抽取参数；
- validation：{"complete":true/false,"missing":[...]}，不规范时按 FactsHelper 回退；
- 答复流：SSE 增量（stream_options.include_usage 可选，缺失保持 missing）。

用量优先使用端点返回值（reported），缺失时按中文字符估算并标注 estimated。
不自动重试（重试属超管编排层职责），网络/协议失败抛 ProviderError。
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx

from harness.budget import DeadlineExceeded
from harness.contracts import AnswerChunk, ModelUsage, RunContext
from harness.providers import ProviderError

from .mock_provider import (
    AGENT_TOOL,
    FactsHelper,
    IntentDetector,
    fallback_args,
    fallback_plan,
)
from .provider import (
    AgentTaskResult,
    BaseMultiAgentProvider,
    PlanResult,
    ValidationVerdict,
)

_API_TIMEOUT_S = 60.0
_ERROR_BODY_LIMIT = 300

_PLAN_SYSTEM = (
    "你是客服工单 Multi-Agent 的超管编排者。"
    "只输出 JSON：{\"intent\":\"意图\",\"agents\":[\"agent1\"]}。"
    "可用专业 Agent：order_agent, logistics_agent, knowledge_agent, validation_agent；"
    "意图 ∈ {logistics_query, delivery_time, order_info, refund_status, "
    "return_policy, exchange_policy, contact_info, complaint, clarification}。"
)
_AGENT_TASK_SYSTEM = (
    "你是客服工单的{agent}。只输出 JSON："
    "{{\"tool\":\"工具名\",\"args\":{{}}}}。"
    "你的负责工具：{tool}；"
    "若问题中缺少必要的订单号/运单号参数，输出 {{\"tool\":null}} 表示需要澄清。"
)
_VALIDATION_SYSTEM = (
    "你是客服工单的聚合校验 Agent。只输出 JSON："
    "{\"complete\":true,\"missing\":[]} 或 "
    "{\"complete\":false,\"missing\":[\"order_id\"]}。"
)


def _first_json_block(content: str) -> dict | None:
    """从模型输出中提取首个 JSON 对象（容忍代码块与前后缀文本）。"""
    text = (content or "").strip()
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start : i + 1])
                    return data if isinstance(data, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _usage_of(usage: dict, prompt_text: str, content: str) -> ModelUsage:
    """优先使用端点返回的 usage；缺失时按中文字符估算（标注来源）。"""
    if usage.get("total_tokens"):
        return ModelUsage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            source="reported",
        )
    prompt = max(1, len(prompt_text) // 2)
    completion = max(1, len(content) // 2)
    return ModelUsage(prompt_tokens=prompt, completion_tokens=completion,
                      total_tokens=prompt + completion, source="estimated")


class OpenAIProvider(BaseMultiAgentProvider):
    """OpenAI 兼容聊天补全 Provider（Multi-Agent 语义决策协议）。"""

    evidence_kind = "provider_stream"

    def __init__(self, settings) -> None:
        if not settings.llm_base_url:
            raise ValueError(
                "LLM_PROVIDER=openai 需要配置 LLM_BASE_URL（兼容端点基础地址）"
            )
        self.settings = settings
        self.model = settings.model_name
        self.base_url = settings.llm_base_url.rstrip("/")
        headers = {"User-Agent": "customer-service-multi-agent/2.0"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        self._client = httpx.AsyncClient(timeout=_API_TIMEOUT_S, headers=headers)
        self._fallback_intent = IntentDetector()

    async def _call(self, messages: list, temperature: float,
                    max_tokens: int) -> tuple[str, dict, float]:
        # 调用点（plan/agent_task/validation）统一传 {"role","content"} 字典
        payload = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": m["content"]}
                         for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        t0 = time.perf_counter()
        try:
            resp = await self._client.post(f"{self.base_url}/chat/completions",
                                           json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"模型请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(
                f"HTTP {resp.status_code}: {resp.text[:_ERROR_BODY_LIMIT]}"
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"响应不是合法 JSON: {exc}") from exc
        latency_ms = (time.perf_counter() - t0) * 1000.0
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        return content, data.get("usage") or {}, latency_ms

    # ---- 超管规划 ----
    async def plan(self, query: str, context: RunContext) -> PlanResult:
        t0 = time.perf_counter()
        messages = [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user",
             "content": f"识别用户意图并为以下问题分配合适的专业 Agent：{query}"},
        ]
        content, usage, _ = await self._call(messages, temperature=0, max_tokens=128)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        mu = _usage_of(usage, " ".join(m["content"] for m in messages), content)
        data = _first_json_block(content)
        if data is not None and data.get("agents"):
            agents = [
                a for a in data.get("agents")
                if a in AGENT_TOOL or a == "validation_agent"
            ]
            return PlanResult(intent=str(data.get("intent") or ""), agents=agents,
                              model=self.model, latency_ms=latency_ms, usage=mu)
        # 规则回退：保证超管规划在语义输出不规范时仍能闭环
        intent = self._fallback_intent.detect(query)
        return PlanResult(intent=intent, agents=fallback_plan(query, intent),
                          fallback=True, model=self.model,
                          latency_ms=latency_ms, usage=mu)

    # ---- 子 Agent 任务决策 ----
    async def agent_task(self, agent: str, query: str,
                         context: RunContext) -> AgentTaskResult:
        tool = AGENT_TOOL.get(agent, "")
        t0 = time.perf_counter()
        messages = [
            {"role": "system",
             "content": _AGENT_TASK_SYSTEM.format(agent=agent, tool=tool or "无")},
            {"role": "user", "content": f"根据用户问题决定是否调用工具：{query}"},
        ]
        content, usage, _ = await self._call(messages, temperature=0, max_tokens=128)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        mu = _usage_of(usage, " ".join(m["content"] for m in messages), content)
        data = _first_json_block(content)
        picked = (data or {}).get("tool")
        if data is not None and picked and picked in AGENT_TOOL.values():
            args = data.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            if picked in ("query_order", "query_logistics") and not (
                args.get("order_id") or args.get("tracking_no")
            ):
                return AgentTaskResult(tool=None, args=args, clarify=True,
                                       model=self.model, latency_ms=latency_ms,
                                       usage=mu)
            return AgentTaskResult(tool=picked, args=args, model=self.model,
                                   latency_ms=latency_ms, usage=mu)
        # 规则回退：默认按 Agent 负责工具抽取参数
        if tool:
            args = fallback_args(query, tool)
            if tool in ("query_order", "query_logistics") and not (
                args.get("order_id") or args.get("tracking_no")
            ):
                return AgentTaskResult(tool=None, args=args, clarify=True,
                                       fallback=True, model=self.model,
                                       latency_ms=latency_ms, usage=mu)
            return AgentTaskResult(tool=tool, args=args, fallback=True,
                                   model=self.model, latency_ms=latency_ms,
                                   usage=mu)
        return AgentTaskResult(tool=None, fallback=True, model=self.model,
                               latency_ms=latency_ms, usage=mu)

    # ---- 聚合校验 ----
    async def validation(self, query: str, facts: dict,
                         context: RunContext) -> ValidationVerdict:
        t0 = time.perf_counter()
        messages = [
            {"role": "system", "content": _VALIDATION_SYSTEM},
            {"role": "user",
             "content": "校验以下聚合事实是否完整（order_id/status/current_location/"
                        f"estimated_delivery）：\n{json.dumps(facts, ensure_ascii=False)}"},
        ]
        content, usage, _ = await self._call(messages, temperature=0, max_tokens=64)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        mu = _usage_of(usage, " ".join(m["content"] for m in messages), content)
        data = _first_json_block(content)
        if data is not None and isinstance(data.get("complete"), bool):
            status = "complete" if data.get("complete") else "incomplete"
            mu.completion_tokens = max(1, len(status) // 2)
            mu.total_tokens = (mu.prompt_tokens or 0) + (mu.completion_tokens or 0)
            return ValidationVerdict(status=status,
                                     missing=list(data.get("missing") or []),
                                     model=self.model, latency_ms=latency_ms,
                                     usage=mu)
        # 规则回退：与 Mock 校验一致（幂等、不虚构事实）
        required = ["order_id", "status", "current_location", "estimated_delivery"]
        present = FactsHelper.extract(facts)
        missing = [k for k in required if not present.get(k)]
        return ValidationVerdict(
            status="complete" if not missing else "incomplete",
            missing=missing, fallback=True, model=self.model,
            latency_ms=latency_ms, usage=mu,
        )

    # ---- 答复流 ----
    async def stream_answer(self, messages: list,
                            context: RunContext) -> AsyncIterator[AnswerChunk]:
        payload = {
            "model": self.model,
            "messages": [
                # 聚合工具观察以 user 消息注入，避免发送缺失调用 ID 的 tool 消息
                {"role": "user" if m.role == "tool" else m.role,
                 "content": (f"[工具结果] {m.content}" if m.role == "tool"
                             else m.content)}
                for m in messages
            ],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": True,
        }
        if getattr(self.settings, "llm_stream_include_usage", True):
            payload["stream_options"] = {"include_usage": True}
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/chat/completions", json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise ProviderError(
                        f"HTTP {resp.status_code}: {body[:_ERROR_BODY_LIMIT]}"
                    )
                usage = None
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if chunk == "[DONE]":
                        # 不提前退出：读完剩余响应体（分块终止符）再自然收尾，
                        # 连接才能归还连接池；提前 break 会把连接整条丢弃，
                        # 每笔答复流后都被迫重建 TCP+TLS 连接
                        continue
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            yield AnswerChunk(content=text)
                    u = data.get("usage")
                    if u:
                        usage = ModelUsage(
                            prompt_tokens=u.get("prompt_tokens"),
                            completion_tokens=u.get("completion_tokens"),
                            total_tokens=u.get("total_tokens"),
                            source="reported",
                        )
                yield AnswerChunk(content="", usage=usage, finish=True)
        except httpx.HTTPError as exc:
            raise ProviderError(f"答复流失败: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()