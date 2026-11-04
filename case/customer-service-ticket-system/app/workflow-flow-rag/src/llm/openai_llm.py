"""OpenAILlm：OpenAI 兼容端点客户端（Workflow 节点的可选真实模型）。

通过环境变量切换真实模型（默认仍为 mock，无需密钥）：
    LLM_PROVIDER=openai
    LLM_MODEL={LLM_API_MODEL_ID}
    LLM_BASE_URL={LLM_API_BASE_URL}   # 兼容端点基础地址，或已含 /chat/completions 的完整地址
    LLM_API_KEY=sk-xxxx               # 可选

两类语义决策接口与 MockLlm 输出协议对齐，模型输出不规范时规则回退：
- recognize_intent：期望 JSON {"intent":"...","confidence":0.98}
- generate_answer / clarify：直接输出答复文本
解析失败时回退 IntentDetector 与固定澄清模板，保证语义决策在模型输出不规范时仍能闭环。
"""
from __future__ import annotations

import time
from typing import AsyncGenerator

import httpx

from ..llm.base import BaseLLMClient, LLMResult, Message, ms
from ..llm.mock_llm import IntentDetector

_API_TIMEOUT = 60.0
_CHAT_COMPLETIONS = "/chat/completions"


def _join_uri(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith(_CHAT_COMPLETIONS):
        return base
    return f"{base}{_CHAT_COMPLETIONS}"


def _first_json_block(content: str) -> dict | None:
    import json
    import re

    text = content.strip()
    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [block.group(1)] if block else []
    candidates.append(text)
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        try:
            data = json.loads(text[first : last + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


class OpenAILlm(BaseLLMClient):
    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.base_url = settings.llm_base_url
        self.api_key = settings.llm_api_key
        if not self.base_url:
            raise ValueError(
                "LLM_PROVIDER=openai 需要配置 LLM_BASE_URL（兼容端点基础地址）"
            )
        self._endpoint = _join_uri(self.base_url)
        self._fallback = IntentDetector()

    def _headers(self) -> dict:
        headers = {"User-Agent": "customer-service-workflow/3.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _payload(self, messages, temperature, max_tokens):
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

    def _call(self, messages, temperature=None, max_tokens=None) -> tuple[str, dict, float]:
        payload = self._payload(
            messages,
            temperature if temperature is not None else self.settings.temperature,
            max_tokens or 512,
        )
        t0 = time.perf_counter()
        with httpx.Client(timeout=_API_TIMEOUT) as client:
            resp = client.post(self._endpoint, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        latency_ms = ms(t0)
        usage = data.get("usage") or {}
        choice = data.get("choices", [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        return content, usage, latency_ms

    def _wrap(self, content: str, prompt_text: str, usage: dict, latency_ms: float) -> LLMResult:
        result = LLMResult(model=self.model, latency_ms=latency_ms, ttft_ms=latency_ms)
        result.content = content
        if usage.get("total_tokens"):
            result.prompt_tokens = int(usage.get("prompt_tokens", 0))
            result.completion_tokens = int(usage.get("completion_tokens", 0))
        else:
            result.prompt_tokens = max(1, len(prompt_text) // 2)
            result.completion_tokens = max(1, len(content) // 2)
        result.total_tokens = result.prompt_tokens + result.completion_tokens
        # v2 用量来源标注：端点返回 reported，缺失按字符估算为 estimated
        result.raw["usage_reported"] = bool(usage.get("total_tokens"))
        return result

    def recognize_intent(self, query: str) -> LLMResult:
        messages = [
            Message(
                role="system",
                content=(
                    "你是客服工单 Workflow 的意图识别节点。只输出 JSON："
                    "{\"intent\":\"意图\",\"confidence\":0.98}。"
                    "意图 ∈ {logistics_query, delivery_time, order_info, refund_status, "
                    "return_policy, exchange_policy, contact_info, complaint, clarification}；"
                    "缺少订单号/运单号时 confidence 取 0.6；投诉意图取 0.5。"
                    # 要素不足的"查单据"类问题必须判 clarification，否则会绕过
                    # 澄清节点直达人工兜底；政策类问题不受此约束
                    # （真实模型冒烟发现，课程正文对应段落）
                    "重要：若用户想查询某笔具体订单或物流的进度/状态，"
                    "但问题中既不含 13 位订单号、也不含运单号，"
                    "intent 必须输出 clarification（confidence 取 0.6），"
                    "不要输出 logistics_query、order_info、delivery_time、"
                    "refund_status 等需要单据定位的查询意图。"
                    "注意：询问政策/流程/规则类问题（如如何退货、退款政策、"
                    "换货流程、客服电话），不需要订单号即可作答，"
                    "按对应意图输出（return_policy / exchange_policy / "
                    "contact_info 等），不要判成 clarification。"
                ),
            ),
            Message(role="user", content=f"识别意图：{query}"),
        ]
        # 推理型模型（如 deepseek-flash）的 reasoning_content 先消耗补全预算，
        # max_tokens 必须覆盖思考链 + 输出，否则 content 为空（实测冒烟结论）
        content, usage, latency_ms = self._call(messages, temperature=0, max_tokens=1000)
        result = self._wrap(content, " ".join(m.content for m in messages), usage, latency_ms)
        data = _first_json_block(content)
        if data is not None and data.get("intent"):
            result.raw = {
                "intent": data["intent"],
                "confidence": float(data.get("confidence", 0.98)),
            }
        else:
            intent = self._fallback.detect(query)
            result.raw = {"intent": intent, "confidence": 0.98, "fallback": True}
        return result

    def generate_answer(self, query: str, facts: dict, retrieval_hits: list) -> LLMResult:
        context_text = ""
        order = ((facts or {}).get("order") or {}).get("order") or {}
        logistics = ((facts or {}).get("logistics") or {}).get("logistics") or {}
        if order:
            context_text += f"订单信息：{order}\n"
        if logistics:
            context_text += f"物流信息：{logistics}\n"
        if retrieval_hits:
            context_text += (
                "知识库命中："
                + "\n".join(h.get("content", "") for h in retrieval_hits[:3])
                + "\n"
            )
        messages = [
            Message(
                role="system",
                content=(
                    "你是客服工单 Workflow 的答复生成节点。"
                    "只能基于提供的结构化事实或知识库内容应答，禁止虚构事实。"
                ),
            ),
            Message(role="user", content=f"{context_text}\n用户问题：{query}"),
        ]
        # 同上：推理型模型需覆盖思考链预算，256 会导致答复为空
        content, usage, latency_ms = self._call(messages, temperature=0, max_tokens=2048)
        return self._wrap(content, " ".join(m.content for m in messages), usage, latency_ms)

    def clarify(self, query: str) -> LLMResult:
        messages = [
            Message(
                role="system",
                content="你是客服工单 Workflow 的澄清节点，要素不足时请用户补充订单号或物流单号。",
            ),
            Message(role="user", content=f"用户问题：{query}"),
        ]
        content, usage, latency_ms = self._call(messages, temperature=0, max_tokens=512)
        return self._wrap(content, " ".join(m.content for m in messages), usage, latency_ms)

    def complete(self, messages, temperature=None, max_tokens=None) -> LLMResult:
        content, usage, latency_ms = self._call(messages, temperature, max_tokens)
        return self._wrap(content, " ".join(m.content for m in messages), usage, latency_ms)

    async def stream(self, messages, temperature=None, max_tokens=None) -> AsyncGenerator[str, None]:
        for chunk in self.stream_sync(messages, temperature, max_tokens):
            yield chunk

    def stream_sync(self, messages, temperature=None, max_tokens=None):
        payload = self._payload(messages, temperature or self.settings.temperature,
                                max_tokens or 512)
        payload["stream"] = True
        with httpx.Client(timeout=_API_TIMEOUT) as client:
            with client.stream("POST", self._endpoint, json=payload,
                               headers=self._headers()) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[len("data:") :].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        import json

                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    token = ((data.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                    if token:
                        yield token