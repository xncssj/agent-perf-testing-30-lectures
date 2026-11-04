"""RAG 检索引擎：知识检索节点 / 知识问答共享入口。

v2 溯源（共享 Harness 设计说明 第 8.2 节）：检索结果记录语料库名、索引版本
（知识文档指纹）、命中文档 ID 与检索耗时；生成引用（citations）由答复节点
从检索结果登记。业务判定只读检索结果本身，不读内部缓存状态。

快照语义（课程快照语义）：发布对象是包含索引、文档数与指纹的完整快照；
search() 在开始时固定一次快照引用，同次检索的 hits／doc_ids／corpus_docs／
index_version 全部来自该快照，写并发期间读者只会看到发布前的完整旧版本或
发布后的完整新版本，不混用。
"""
from __future__ import annotations

import hashlib
import threading
import time

from prometheus_client import Counter, Histogram

from ..models.store import DataStore
from .index import RagIndex

# RAG 链路专属指标（观测点与业务路径旁路，检索失败不阻断业务）：
# - hit/empty 序列预建 0 值，未走知识意图时不出样本，看板口径为「检索速率」而非请求数
# - 桶按进程内 BM25 毫秒级实测设计（0.5ms~500ms）
RAG_SEARCH_TOTAL = Counter(
    "workflow_rag_search_total",
    "RAG 知识检索次数（status=hit 命中 / empty 空结果）",
    ["status"],
)
RAG_SEARCH_TOTAL.labels(status="hit")
RAG_SEARCH_TOTAL.labels(status="empty")
RAG_SEARCH_LATENCY = Histogram(
    "workflow_rag_search_latency_seconds",
    "RAG 知识检索耗时（RagEngine.search 内部计时）",
    buckets=(0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
)


def _index_version(docs: list[dict]) -> str:
    """知识语料指纹：规范化 doc_id + 正文哈希前 8 位（语料或版本变化即变化）。

    不把进程内数字 ID 当语料身份；加载顺序重排不改变同一语料的指纹。
    """
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda x: x.get("doc_id", "")):
        h.update((d.get("doc_id", "") + "\x00" + (d.get("content") or "")).encode("utf-8"))
    return h.hexdigest()[:8]


class _Snapshot:
    """不可变索引快照：索引、文档数与版本指纹同源发布。"""

    __slots__ = ("index", "corpus_docs", "index_version")

    def __init__(self, index: RagIndex, corpus_docs: int, index_version: str) -> None:
        self.index = index
        self.corpus_docs = corpus_docs
        self.index_version = index_version


class RagEngine:
    def __init__(self, store: DataStore, top_k: int = 3,
                 knowledge_base: str = "customer-service-kb") -> None:
        self.store = store
        self.top_k = top_k
        self.knowledge_base = knowledge_base
        self._snapshot: _Snapshot | None = None

    def warm(self) -> None:
        """服务启动预热：用当前文档集合发布初始快照。"""
        docs = []
        for doc in self.store.knowledge_docs():
            item = dict(doc)
            item["doc_id"] = DataStore.doc_id(doc)
            docs.append(item)
        self.publish(docs)

    def publish(self, docs: list[dict]) -> str:
        """在候选文档上构建完整索引快照并发布，返回新索引版本。

        候选文档须已带 doc_id。构建失败抛出异常时，当前快照保持不变，
        由服务层保证此时内存文档同样未替换（失败不部分写入）。
        """
        index = RagIndex()
        index.build(docs)  # 先构建：失败时未发布任何内容
        snapshot = _Snapshot(index, len(docs), _index_version(docs))
        self._snapshot = snapshot  # 原子替换快照引用（提交点）
        return snapshot.index_version

    @property
    def index_version(self) -> str:
        """当前已发布快照的语料指纹；未发布过时为空串。"""
        snapshot = self._snapshot
        return snapshot.index_version if snapshot else ""

    def search(self, query: str, top_k: int | None = None) -> dict:
        # 开始时固定一次快照引用：整个调用的结果同源于这一个版本
        snapshot = self._snapshot
        k = top_k or self.top_k
        t0 = time.perf_counter()
        hits = snapshot.index.search(query, top_k=k) if snapshot else []
        latency_ms = (time.perf_counter() - t0) * 1000.0
        RAG_SEARCH_LATENCY.observe(latency_ms / 1000.0)
        RAG_SEARCH_TOTAL.labels(status="hit" if hits else "empty").inc()
        return {
            "hits": hits,
            "doc_ids": [h.get("doc_id") for h in hits],
            "top_k": k,
            "latency_ms": round(latency_ms, 3),
            # v2 溯源字段：语料库与索引版本（共享 Harness 设计说明 第 8.2 节）；
            # 与 hits 同源自开始时固定的快照
            "corpus": self.knowledge_base,
            "index_version": snapshot.index_version if snapshot else "",
            "corpus_docs": snapshot.corpus_docs if snapshot else 0,
        }
