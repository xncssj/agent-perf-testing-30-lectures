"""业务工具参数模型：Schema 校验的唯一事实源。

参数模型禁止未声明字段；执行器在调用工具前用它完成校验，
并据此生成原生工具调用的 JSON Schema（共享 Harness 设计说明 第 4.2 节）。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class QueryOrderArgs(BaseModel):
    """订单查询参数：要求非空 order_id。"""

    model_config = {"extra": "forbid"}

    order_id: str = Field(min_length=1)


class QueryLogisticsArgs(BaseModel):
    """物流查询参数：tracking_no 或 order_id 至少一项；
    同时提供时沿用 tracking_no 优先（与工具实现保持一致）。"""

    model_config = {"extra": "forbid"}

    tracking_no: Optional[str] = None
    order_id: Optional[str] = None

    @model_validator(mode="after")
    def _need_one(self) -> "QueryLogisticsArgs":
        if not (self.tracking_no or "").strip() and not (self.order_id or "").strip():
            raise ValueError("tracking_no 与 order_id 至少提供一项")
        return self


class SearchKnowledgeArgs(BaseModel):
    """知识库检索参数：要求非空 query；top_k 由服务端配置注入，模型不可改。"""

    model_config = {"extra": "forbid"}

    query: str = Field(min_length=1)


# 原生工具定义的 JSON Schema（描述与参数名与工具实现一致）
TOOL_SCHEMAS: dict[str, dict] = {
    "query_order": {
        "description": "查询订单信息，需要订单ID",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "订单ID"}},
            "required": ["order_id"],
        },
    },
    "query_logistics": {
        "description": "查询物流状态，需要物流单号或订单ID",
        "parameters": {
            "type": "object",
            "properties": {
                "tracking_no": {"type": "string", "description": "物流单号"},
                "order_id": {"type": "string", "description": "订单ID"},
            },
        },
    },
    "search_knowledge": {
        "description": "搜索知识库，需要搜索关键词",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"],
        },
    },
}
