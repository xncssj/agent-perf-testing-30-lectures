"""知识库检索工具：按关键词在 FAQ / 政策中检索并取 top_k 命中。

检索实现使用简单的关键词打分（可运行、零外部依赖）；课程 23-24 讲
升级为 Workflow + RAG 后替换为向量检索。
"""
from __future__ import annotations

from ..tools.base import BaseTool
from ..tools.params import SearchKnowledgeArgs


class KnowledgeTool(BaseTool):
    name = "search_knowledge"
    description = "搜索知识库，需要搜索关键词"
    args_model = SearchKnowledgeArgs

    def __init__(self, repo, config: dict | None = None) -> None:
        super().__init__(config)
        self._repo = repo
        self.top_k = int((config or {}).get("top_k", 3))

    def _run(self, query: str = "", **kwargs) -> dict:
        if not query:
            raise ValueError("缺少 query")
        docs = self._repo.search_knowledge(query, top_k=self.top_k)
        return {"found": bool(docs), "hits": docs, "query": query}