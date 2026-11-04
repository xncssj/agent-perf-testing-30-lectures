"""Harness 最小公共契约（v2）。

统一消息、调用、观察、运行输入、运行上下文、运行结果与事件类型，
供运行器、Provider、工具执行器、API 与评测采集共同使用。

口径约定（见 ../../../共享 Harness 设计说明 第 4 节）：
- API/事件 schema_version=2；task_id 缺省时生成独立 UUID；
  batch_id 缺省为空，不为交互请求虚构实验批次。
- 每个工具调用、观察与事件由 tool_call_id 关联；run_id 由服务端生成 UUID。
- 运行完成不直接等于业务合格：终态集合见 RunStatus/StopReason。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = 2

# 运行终态集合
RunStatus = Literal[
    "completed", "failed", "timed_out", "budget_exhausted", "cancelled"
]

# 终态下的停止原因（示例集合，允许 Provider/工具补充明确原因）
StopReason = str

STOP_ANSWERED = "answered"
STOP_CLARIFICATION = "clarification"
STOP_NOT_FOUND = "not_found"
STOP_HANDOFF = "handoff_requested"
STOP_PROVIDER_ERROR = "provider_error"
STOP_INVALID_DECISION = "invalid_decision"
STOP_TOOL_FAILED = "tool_failed"
STOP_EMPTY_ANSWER = "empty_answer"
STOP_REQUEST_DEADLINE = "request_deadline"
STOP_MAX_ITERATIONS = "max_iterations"
STOP_MAX_TOOL_CALLS = "max_tool_calls"
STOP_CLIENT_DISCONNECT = "client_disconnect"
STOP_CALLER_CANCELLED = "caller_cancelled"


def new_id() -> str:
    """生成无连字符的 UUID，用作 run_id / task_id / tool_call_id。"""
    return uuid.uuid4().hex


class ToolCall(BaseModel):
    """一次工具调用：arguments 必须是对象，解析与验证失败均有明确错误。"""

    id: str = Field(default_factory=new_id)
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """运行内历史消息；工具观察必须通过 tool_call_id 关联调用。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: str = ""


class ModelUsage(BaseModel):
    """一次模型调用的用量与来源。"""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    # reported=Provider 实际返回；estimated=模拟估算；missing=缺失
    source: Literal["reported", "estimated", "missing"] = "missing"


class Decision(BaseModel):
    """一次模型决策：动作 + 调用列表 + 用量。

    动作四类：call_tools / finalize / clarify / handoff。
    控制动作（finalize/clarify/handoff）不计工具执行尝试，但决策计一轮。
    """

    action: Literal["call_tools", "finalize", "clarify", "handoff"]
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # 原生模式下表达控制动作的函数调用（finish/ask_clarification/complaint），
    # 需要在历史中配对观察消息（无业务副作用）
    control_calls: list[ToolCall] = Field(default_factory=list)
    content: str = ""
    model: str = ""
    latency_ms: float = 0.0
    ttft_ms: Optional[float] = None
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ToolObservation(BaseModel):
    """一次工具调用的最终观察。

    查询无结果保留 data 中 found=false，不伪装成执行异常；
    rejected=true 表示参数校验拒绝，未产生实际执行。

    attempt 为**本次逻辑调用的第几次尝试**（1=首次；超时自动重试时递增，
    故最终观察的 attempt 即该调用的总尝试次数）。跨调用的全局尝试计数
    （含重试放大）由预算承担，见 metrics.tool_attempts。
    """

    tool_call_id: str
    name: str
    attempt: int = 1
    data: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_kind: Optional[str] = None
    duration_ms: float = 0.0
    timed_out: bool = False
    rejected: bool = False


class AnswerChunk(BaseModel):
    """最终答复的一个文本增量；usage 仅在 Provider 可提供时非空。"""

    content: str = ""
    usage: Optional[ModelUsage] = None
    finish: bool = False


class RunInput(BaseModel):
    """一次业务运行的输入身份。评测必须传固定工单 ID（task_id）。"""

    query: str
    task_id: str = ""
    conversation_id: str = ""
    batch_id: str = ""


class RunContext(BaseModel):
    """运行内共享上下文：身份、开始时间与截止时间（单调时钟）。"""

    task_id: str
    run_id: str
    batch_id: str = ""
    conversation_id: str = ""
    query: str = ""
    started_at: float = Field(default_factory=time.monotonic)
    deadline: float = 0.0

    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def remaining_ms(self) -> float:
        return self.remaining_s() * 1000.0


class RunResult(BaseModel):
    """运行终态结果。运行完成不直接等于业务合格。"""

    schema_version: int = SCHEMA_VERSION
    task_id: str
    run_id: str
    batch_id: str = ""
    status: RunStatus
    stop_reason: str = ""
    response: str = ""
    intent: str = ""
    error: Optional[str] = None
    started_at_epoch_ms: int = 0
    ended_at_epoch_ms: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    """内部事件公共字段。seq 在同一次运行内严格递增，
    ts_ms 为单调时钟相对运行开始的毫秒时间，ts_wall 为内部记录的墙钟时间。"""

    schema_version: int = SCHEMA_VERSION
    task_id: str
    run_id: str
    batch_id: str = ""
    seq: int
    ts_ms: float
    ts_wall: float = Field(default_factory=time.time)
    type: str


class ThinkingEvent(Event):
    type: Literal["thinking"] = "thinking"
    content: str = ""


class ToolCallEvent(Event):
    type: Literal["tool_call"] = "tool_call"
    tool: str
    tool_call_id: str
    attempt: int = 1
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(Event):
    type: Literal["tool_result"] = "tool_result"
    tool: str
    tool_call_id: str
    attempt: int = 1
    data: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: float = 0.0


class AnswerEvent(Event):
    type: Literal["answer"] = "answer"
    content: str
    source: str = "model"  # model / template


class DoneEvent(Event):
    type: Literal["done"] = "done"
    status: RunStatus
    stop_reason: str = ""
    response: str = ""
    intent: str = ""
    error: Optional[str] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)


# 内部观测事件（不进入 SSE 公共流，仅供 Langfuse 等采集器消费）
class ModelCallEvent(Event):
    type: Literal["model_call"] = "model_call"
    call_kind: str = "decision"  # decision / final_answer
    model: str = ""
    latency_ms: float = 0.0
    usage: ModelUsage = Field(default_factory=ModelUsage)


def tool_definitions(params_schemas: dict[str, dict]) -> list[dict]:
    """从工具参数 JSON Schema 构建原生工具定义列表。"""
    return [
        {"type": "function", "function": {"name": name, **schema}}
        for name, schema in params_schemas.items()
    ]
