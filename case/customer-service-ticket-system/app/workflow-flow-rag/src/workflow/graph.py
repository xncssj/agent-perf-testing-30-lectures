"""Workflow 图定义：节点清单与 DAG 边（Workflow 图设计说明）。"""
from __future__ import annotations

from ..workflow.engine import Edge, NodeDef
from ..workflow.nodes import CLARIFY_INTENT, DATA_INTENTS, KNOWLEDGE_INTENTS


def build_graph() -> tuple[list[NodeDef], list[Edge], str]:
    nodes = [
        NodeDef(id="intent_recognition", type="intent_recognition", name="意图识别"),
        NodeDef(id="data_retrieval", type="data_retrieval", name="业务数据检索"),
        NodeDef(id="knowledge_retrieval", type="knowledge_retrieval", name="知识库检索"),
        NodeDef(id="response_generation", type="response_generation", name="答复生成"),
        NodeDef(id="confidence_check", type="confidence_check", name="置信度与护栏判定",
                config={"threshold": 0.8}),
        NodeDef(id="clarification", type="clarification", name="澄清"),
    ]
    data_cond = f"intent in {list(sorted(DATA_INTENTS))!r}"
    know_cond = f"intent in {list(sorted(KNOWLEDGE_INTENTS))!r}"
    clarify_cond = f"intent == {CLARIFY_INTENT!r}"
    edges = [
        Edge("intent_recognition", "data_retrieval", condition=data_cond),
        Edge("intent_recognition", "knowledge_retrieval", condition=know_cond),
        Edge("intent_recognition", "clarification", condition=clarify_cond),
        # default 兜底：未匹配上述意图（如投诉/未知）进入数据检索
        Edge("intent_recognition", "data_retrieval", condition=None),
        Edge("data_retrieval", "response_generation", condition=None),
        Edge("knowledge_retrieval", "response_generation", condition=None),
        Edge("response_generation", "confidence_check", condition=None),
    ]
    return nodes, edges, "intent_recognition"