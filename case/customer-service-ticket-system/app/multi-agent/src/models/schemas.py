"""API 数据模型（Pydantic，v2）。

ChatRequest 增加可选 task_id / batch_id，用于评测身份传递；
conversation_id 仅作关联，每次运行的消息历史相互隔离。
请求体旧 stream 字段仍接受，但响应形式由 URL 决定（见 api/routes.py）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: str = Field(default="", description="会话ID（仅作关联）")
    user_query: str = Field(..., description="用户问题")
    task_id: str = Field(default="", description="任务ID（评测传固定工单 ID）")
    batch_id: str = Field(default="", description="批次ID（压测批次关联）")
    stream: bool = Field(default=True, description="旧字段：响应形式由 URL 决定")


class ToolCallInfo(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    timed_out: bool = False
    error: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class Success(BaseModel):
    success: bool = True
    message: str = "ok"
