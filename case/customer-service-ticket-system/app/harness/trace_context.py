"""依赖调用上下文（006 契约 §2）：向 Java 等内部 HTTP 依赖传播可校验身份。

- 入口（工具执行器）生成可信上下文；依赖服务只把相关 ID 写入结构化
  日志/Span，不把逐笔 ID 作为 Prometheus 指标标签；
- Trace ID 与 run_id 可逆：W3C ``traceparent`` 的 trace-id 直接取
  ``run_id``（``new_id()`` 为 32 位无连字符 hex，天然满足 32 hex 约束）；
  span-id 取 tool_call_id 前 16 位 hex，完整 tool_call_id 经
  ``x-agent-tool-call-id`` 头携带，便于依赖侧回查；
- 传播失败（如依赖未记录）不影响业务，只意味着关联缺口，不得伪造
  完整链路。

上下文经 :mod:`contextvars` 传递；工具执行器把当前上下文快照带入
工作线程（``ThreadPoolExecutor`` 不自动传播 contextvars），工具内部
通过 :func:`dependency_headers` 读取。
"""
from __future__ import annotations

from contextvars import ContextVar, Token

# 值为 {"run_id": str, "tool_call_id": str} 或 None
_current: ContextVar[dict | None] = ContextVar(
    "agent_dependency_context", default=None
)


def traceparent(run_id: str, tool_call_id: str) -> str:
    """生成 W3C Trace Context 头：00-{trace-id}-{span-id}-{flags}。"""
    trace_id = (run_id or "").replace("-", "")
    if len(trace_id) != 32:
        trace_id = trace_id.ljust(32, "0")[:32]
    span_id = (tool_call_id or "").replace("-", "")[:16].ljust(16, "0")
    return f"00-{trace_id}-{span_id}-01"


def set_dependency_context(run_id: str, tool_call_id: str) -> Token:
    """登记当前工具尝试的依赖上下文；调用方负责 reset。"""
    return _current.set({"run_id": run_id, "tool_call_id": tool_call_id})


def reset_dependency_context(token: Token) -> None:
    _current.reset(token)


def dependency_headers() -> dict[str, str]:
    """读取当前上下文的传播头；无上下文时返回空（不伪造身份）。"""
    ctx = _current.get()
    if not ctx:
        return {}
    run_id = ctx.get("run_id") or ""
    tool_call_id = ctx.get("tool_call_id") or ""
    headers = {
        "traceparent": traceparent(run_id, tool_call_id),
        "x-agent-run-id": run_id,
    }
    if tool_call_id:
        headers["x-agent-tool-call-id"] = tool_call_id
    return headers
