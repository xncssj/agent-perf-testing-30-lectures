"""共享数据仓库：启动时从 test-data 只读加载业务快照。

三阶段共用同一基准数据集（test-data/MANIFEST.md 单一事实源）。
Workflow+RAG 阶段以"进程内只读快照"承载订单/物流/知识库数据：
- 无外部 PostgreSQL/Milvus 依赖，压测期间数据固定不变；
- 字典结构天然无跨线程写竞态（对比案例一 SQLite 连接竞态）。
生产环境对应 PostgreSQL + Milvus/ES，本实现是其最小可复现替身。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class DataStore:
    """订单、物流、知识库的快照与查询接口（支持 CRUD 操作）。"""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._orders: dict[str, dict] = {}
        self._logistics_by_order: dict[str, dict] = {}
        self._logistics_by_tracking: dict[str, dict] = {}
        self._kb: list[dict] = []
        self._kb_next_id = 1
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        self._load_orders()
        self._load_logistics()
        self._load_knowledge()

    def _load_orders(self) -> None:
        path = self._data_dir / "orders.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        orders = data if isinstance(data, list) else data.get("orders", [])
        for o in orders:
            oid = str(o.get("order_id", ""))
            if oid:
                self._orders[oid] = o

    def _load_logistics(self) -> None:
        path = self._data_dir / "logistics.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        logistics = data if isinstance(data, list) else data.get("logistics", [])
        for lo in logistics:
            oid = str(lo.get("order_id", ""))
            track = str(lo.get("tracking_no", ""))
            if oid:
                self._logistics_by_order[oid] = lo
            if track:
                self._logistics_by_tracking[track] = lo

    def _load_knowledge(self) -> None:
        kb_dir = self._data_dir / "knowledge-base"
        docs: list[dict] = []
        # 按 faq.json → policies.json → products.json 固定加载顺序补齐缺失数字 ID；
        # 语料中已有的合法 ID 保留，下一个可用 ID 取历史最大值 + 1（删除后不复用）
        next_id = 1
        for fname in ("faq.json", "policies.json", "products.json"):
            path = kb_dir / fname
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("documents", data)
            if isinstance(items, dict):
                items = list(items.values())
            for item in items:
                doc = dict(item)
                doc_id = doc.get("id")
                if not isinstance(doc_id, int) or doc_id <= 0:
                    doc["id"] = next_id
                next_id = max(next_id, doc["id"]) + 1
                docs.append(doc)
        self._kb = docs
        self._kb_next_id = next_id

    @staticmethod
    def doc_id(doc: dict) -> str:
        """文档稳定 ID：category:title（RAG 检索质量测试按此断言）。"""
        cat = doc.get("category", "kb")
        title = doc.get("title", "untitled")
        return f"{cat}:{title}"

    def get_order(self, order_id: str) -> dict | None:
        return self._orders.get(str(order_id))

    def get_logistics_by_order(self, order_id: str) -> dict | None:
        return self._logistics_by_order.get(str(order_id))

    def get_logistics_by_tracking(self, tracking_no: str) -> dict | None:
        return self._logistics_by_tracking.get(str(tracking_no))

    def knowledge_docs(self) -> list[dict]:
        """文档副本快照：调用方持有副本，不会污染存储内文档。"""
        return [dict(d) for d in self._kb]

    # ---- 知识库读取 ----

    def list_knowledge(self) -> list[dict]:
        """列出所有知识文档（副本）。"""
        with self._lock:
            return [dict(d) for d in self._kb]

    def get_knowledge_by_id(self, kid: int) -> dict | None:
        """根据 ID 获取单条知识文档（副本）。"""
        with self._lock:
            for doc in self._kb:
                if doc.get("id") == kid:
                    return dict(doc)
        return None

    def next_knowledge_id(self) -> int:
        """当前下一个可用数字 ID（删除后不复用）。"""
        with self._lock:
            return self._kb_next_id

    def replace_knowledge(self, docs: list[dict], next_id: int) -> None:
        """一次性替换内存文档集合与下一个可用 ID。

        服务层写锁内调用：调用方传入的应是候选副本，本方法再拷贝一层持有；
        候选构建失败时不会走到这里，内存文档保持写入前状态。
        """
        with self._lock:
            self._kb = [dict(d) for d in docs]
            self._kb_next_id = next_id

    def search_knowledge(self, query: str, top_k: int = 3) -> list[dict]:
        """知识库检索：基于关键词打分。"""
        query_lower = query.lower()
        scored: list[tuple[float, dict]] = []
        for doc in self._kb:
            keywords = [str(k).lower() for k in doc.get("keywords", [])]
            title = str(doc.get("title", ""))
            content = str(doc.get("content", ""))
            score = 0.0
            # 关键词命中
            for kw in keywords:
                if kw and (query_lower in content.lower() or kw in query_lower):
                    score += 1.0
            # 标题命中
            if query_lower in title.lower():
                score += 1.0
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"rank": i + 1, **d} for i, (_, d) in enumerate(scored[:top_k])]