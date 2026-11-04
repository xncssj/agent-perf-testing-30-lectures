"""OpenAIProvider：OpenAI 兼容端点 Provider（原生工具 / JSON 兼容双协议）。

协议转换留在 Provider 内（共享 Harness 设计说明 第 6.1 节）：
- native：提交业务工具与控制函数（finish/ask_clarification/complaint，
  无业务副作用）的 JSON Schema，解析 tool_calls 为统一决策；
  端点不支持原生工具或返回非法协议时明确失败（ProviderError），
  不自动降级后重复请求。
- json：保留 v1 的 tool_selection 决策提示词，把内部调用与观察序列化为
  协议合法的 user/assistant 消息；非法 JSON 按 invalid_decision 处理，
  不按“无工具可调用”结束。

两种模式共享 stream_answer 答复流。用量为端点实际返回（reported）；
流式答复仅在端点返回用量时记录，缺失时保持 missing，不用片段数冒充。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

import httpx

from .budget import DeadlineExceeded
from .contracts import (
    AnswerChunk,
    Decision,
    Message,
    ModelUsage,
    RunContext,
    ToolCall,
)
from .providers import (
    CONTROL_TOOL_SCHEMAS,
    BaseProvider,
    InvalidDecisionError,
    ProviderError,
)

_API_TIMEOUT_S = 60.0
_ERROR_BODY_LIMIT = 300

# 控制函数 -> 决策动作
_CONTROL_ACTION = {
    "finish": "finalize",
    "ask_clarification": "clarify",
    "complaint": "handoff",
}


class OpenAIProvider(BaseProvider):
    """OpenAI 兼容聊天补全 Provider。"""

    evidence_kind = "provider_stream"  # 流式答复为 Provider 实测片段

    def __init__(self, settings, prompt_manager, mode: str = "native") -> None:
        if mode not in ("native", "json"):
            raise ValueError(f"未知工具调用模式: {mode}")
        if not settings.llm_base_url or not settings.llm_api_key:
            raise ValueError(
                "LLM_PROVIDER=openai 需要同时配置 LLM_BASE_URL 与 LLM_API_KEY"
            )
        self.settings = settings
        self.prompt_manager = prompt_manager
        self.mode = mode
        self.model = settings.model_name
        self.base_url = settings.llm_base_url.rstrip("/")
        # 推理档位（S7R-R2，2026-10-14）：空=不传（端点默认）；三处 payload 统一经本属性注入
        self.reasoning_effort = (getattr(settings, "llm_reasoning_effort", "") or "").lower()
        self._client = httpx.AsyncClient(
            timeout=_API_TIMEOUT_S,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        )

    # ---- 决策 ----
    async def decide(
        self,
        messages: list[Message],
        tools: dict[str, dict],
        context: RunContext,
    ) -> Decision:
        t0 = time.perf_counter()
        try:
            if self.mode == "native":
                decision = await self._decide_native(messages, tools)
            else:
                decision = await self._decide_json(messages, tools)
        except (ProviderError, InvalidDecisionError):
            raise
        except DeadlineExceeded:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"模型调用失败: {exc}") from exc
        decision.latency_ms = (time.perf_counter() - t0) * 1000.0
        decision.model = self.model
        return decision

    async def _decide_native(self, messages: list[Message],
                             tools: dict[str, dict]) -> Decision:
        wire = [_to_wire(m) for m in messages]
        tool_defs = [
            {"type": "function", "function": {"name": name, **schema}}
            for name, schema in tools.items()
        ] + [
            {"type": "function", "function": {"name": name, **schema}}
            for name, schema in CONTROL_TOOL_SCHEMAS.items()
        ]
        data = await self._post_chat({
            "model": self.model,
            "messages": wire,
            "tools": tool_defs,
            "tool_choice": "auto",
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
            **({"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}),
        })
        usage = _usage_of(data, "reported")
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        raw_calls = message.get("tool_calls") or []
        actions: set[str] = set()
        decision = Decision(action="finalize", usage=usage)
        for rc in raw_calls:
            fn = rc.get("function") or {}
            name = fn.get("name") or ""
            if name in _CONTROL_ACTION:
                actions.add(_CONTROL_ACTION[name])
                continue
            if name in tools:
                decision.tool_calls.append(ToolCall(
                    id=rc.get("id") or ToolCall().id,
                    name=name,
                    arguments=_parse_arguments(fn.get("arguments")),
                ))
            else:
                raise InvalidDecisionError(f"未声明的工具调用: {name}")
        if actions:
            if decision.tool_calls:
                raise InvalidDecisionError("一次响应不能混合控制动作与查询调用")
            if len(actions) > 1:
                raise InvalidDecisionError(f"控制动作相互冲突: {sorted(actions)}")
            decision.action = actions.pop()
        elif decision.tool_calls:
            decision.action = "call_tools"
        else:
            # 原生模式没有工具调用时视为 finalize，交给客服策略执行最终答复
            decision.action = "finalize"
            decision.content = message.get("content") or ""
        # 保留控制函数调用原文，供运行器在历史中配对观察消息
        decision.control_calls = [
            ToolCall(
                id=rc.get("id") or ToolCall().id,
                name=(rc.get("function") or {}).get("name") or "",
                arguments=_parse_arguments((rc.get("function") or {}).get("arguments")),
            )
            for rc in raw_calls
            if (rc.get("function") or {}).get("name") in _CONTROL_ACTION
        ]
        return decision

    async def _decide_json(self, messages: list[Message],
                           tools: dict[str, dict]) -> Decision:
        query = ""
        for m in messages:
            if m.role == "user":
                query = m.content
                break
        wire: list[dict] = []
        observation_json = ""
        for m in messages:
            if m.role == "system":
                wire.append({"role": "system", "content": m.content})
            elif m.role == "user":
                continue  # 查询经 tool_selection 提示词注入，避免重复
            elif m.role == "assistant":
                if m.tool_calls:
                    call = m.tool_calls[0]
                    wire.append({"role": "assistant", "content": json.dumps(
                        {"tool": call.name, "args": call.arguments},
                        ensure_ascii=False,
                    )})
                elif m.content:
                    wire.append({"role": "assistant", "content": m.content})
            elif m.role == "tool":
                observation_json = m.content
        selector = self.prompt_manager.tool_selection(query)
        if observation_json:
            selector = f"工具执行结果：{observation_json}\n\n{selector}"
        wire.append({"role": "user", "content": selector})
        data = await self._post_chat({
            "model": self.model,
            "messages": wire,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
            **({"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}),
        })
        usage = _usage_of(data, "reported")
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        try:
            parsed = json.loads(content.strip())
        except json.JSONDecodeError as exc:
            # 非法 JSON 不按“无工具可调用”结束
            raise InvalidDecisionError(f"决策不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise InvalidDecisionError("决策 JSON 必须是对象")
        tool_name = parsed.get("tool")
        args = parsed.get("args") or {}
        if not isinstance(args, dict):
            raise InvalidDecisionError("决策 args 必须是对象")
        decision = Decision(action="finalize", content=content, usage=usage)
        if tool_name in _CONTROL_ACTION:
            decision.action = _CONTROL_ACTION[tool_name]
        elif tool_name in tools:
            decision.action = "call_tools"
            decision.tool_calls = [ToolCall(name=tool_name, arguments=args)]
        elif tool_name:
            raise InvalidDecisionError(f"未声明的工具调用: {tool_name}")
        return decision

    # ---- 答复流 ----
    async def stream_answer(
        self,
        messages: list[Message],
        context: RunContext,
    ) -> AsyncIterator[AnswerChunk]:
        wire = [_generation_wire(m) for m in messages]
        payload = {
            "model": self.model,
            "messages": wire,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": True,
            **({"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}),
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

    # ---- 共享 ----
    async def _post_chat(self, payload: dict) -> dict:
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions", json=payload
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"模型请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(
                f"HTTP {resp.status_code}: {resp.text[:_ERROR_BODY_LIMIT]}"
            )
        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(f"响应不是合法 JSON: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


def _to_wire(m: Message) -> dict:
    """内部消息 -> OpenAI 协议消息（决策阶段）。"""
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for c in m.tool_calls
            ],
        }
    if m.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": m.tool_call_id or "",
            "content": m.content,
        }
    return {"role": m.role, "content": m.content}


def _generation_wire(m: Message) -> dict:
    """内部消息 -> OpenAI 协议消息（答复生成阶段）。

    聚合工具观察以 user 消息注入，避免发送缺失调用 ID 的 tool 消息。
    """
    if m.role == "tool":
        return {"role": "user", "content": f"[工具结果] {m.content}"}
    return {"role": m.role, "content": m.content}


def _parse_arguments(raw) -> dict:
    """解析原生调用参数字符串；失败返回空对象，由参数模型校验给出明确错误。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _usage_of(data: dict, source: str) -> ModelUsage:
    u = data.get("usage") or {}
    return ModelUsage(
        prompt_tokens=u.get("prompt_tokens"),
        completion_tokens=u.get("completion_tokens"),
        total_tokens=u.get("total_tokens"),
        source=source if u else "missing",
    )
