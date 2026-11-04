"""API 请求/响应模式。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="用户问题")
    execution_id: str | None = Field(default=None, description="幂等执行 ID（检查点续跑用）")
    task_id: str = Field(default="", description="任务ID（评测传固定工单 ID）")
    batch_id: str = Field(default="", description="批次ID（压测批次关联）")


class ChatResponse(BaseModel):
    request_id: str
    response: str
    path: list[str]
    branch: str
    intent: str = ""
    confidence: float = 0.0
    e2e_ms: float
    total_tokens: int = 0
    status: str
    error: str | None = None
    trace: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    provider: str
    kb_docs: int = 0
    orders: int = 0
    logistics: int = 0