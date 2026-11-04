"""SQLite 数据访问层：订单与物流快照、知识库语料的加载与查询。

案例二（Multi-Agent）相对案例一的关键改造：**每请求独立只读连接**。

案例一使用单个 check_same_thread=False 连接被所有压测线程共享，
高并发读写切换时出现 SQLite 竞态（本轮在容量与稳定性测试中观察到）。
案例二改为每笔业务请求内部短生命周期只读连接（mode=ro），
连接随请求创建、随请求关闭，写入只在启动初始化阶段发生，
从根本上消除并发读写切换的竞态窗口。

压测前由 scripts/init_db.py 或服务启动时导入 tests/test_data 的快照，
运行期间数据保持不变，确保基线可复现。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


KNOWLEDGE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]'
)
"""
ORDERS_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT,
    status TEXT,
    items TEXT,
    total_amount REAL,
    shipping_address TEXT,
    tracking_no TEXT,
    carrier TEXT,
    create_time TEXT,
    update_time TEXT
)
"""
LOGISTICS_SQL = """
CREATE TABLE IF NOT EXISTS logistics (
    tracking_no TEXT PRIMARY KEY,
    order_id TEXT,
    carrier TEXT,
    status TEXT,
    current_location TEXT,
    origin TEXT,
    destination TEXT,
    trajectory TEXT,
    estimated_delivery TEXT
)
"""


def _as_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else (value or "")


class Database:
    """SQLite 快照访问：读请求使用独立只读连接，写只发生在初始化阶段。

    Database 实例本身不持有任何跨线程共享连接；每次 get_* / search_*
    都通过 _read() 打开一条只读连接并立刻关闭，因此支持被多个压测线程
    同时调用而不产生 SQLite 竞态（mode=ro 也禁止了运行期任何写操作）。
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self._init_schema()

    def _connect(self, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False
            )
        else:
            # 写连接仅在 schema 初始化与 seed 导入时短命使用
            conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect(read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        conn = self._connect(read_only=False)
        try:
            conn.execute(ORDERS_SQL)
            conn.execute(LOGISTICS_SQL)
            conn.execute(KNOWLEDGE_SQL)
            conn.commit()
        finally:
            conn.close()

    def seed_loads(self, orders: list[dict], logistics: list[dict], knowledge: list[dict]) -> None:
        """导入快照数据（先清空再写入，保证与数据文件一致）。"""
        conn = self._connect(read_only=False)
        try:
            for table in ("orders", "logistics", "knowledge"):
                conn.execute(f"DELETE FROM {table}")
            for o in orders:
                conn.execute(
                    "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        o["order_id"], o.get("user_id"), o.get("status"),
                        _as_text(o.get("items")), o.get("total_amount"),
                        _as_text(o.get("shipping_address")), o.get("tracking_no"),
                        o.get("carrier"), o.get("create_time"), o.get("update_time"),
                    ),
                )
            for lo in logistics:
                conn.execute(
                    "INSERT INTO logistics VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        lo["tracking_no"], lo.get("order_id"), lo.get("carrier"),
                        lo.get("status"), lo.get("current_location"), lo.get("origin"),
                        lo.get("destination"), _as_text(lo.get("trajectory")),
                        lo.get("estimated_delivery"),
                    ),
                )
            for k in knowledge:
                conn.execute(
                    "INSERT INTO knowledge (category, title, content, keywords) VALUES (?,?,?,?)",
                    (
                        k.get("category", "faq"), k.get("title"),
                        k.get("content"), _as_text(k.get("keywords", [])),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    ###########################################################################
    # 查询（每条查询使用独立只读连接，随用随关）
    ###########################################################################
    def get_order(self, order_id: str) -> dict | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_logistics_by_tracking(self, tracking_no: str) -> dict | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM logistics WHERE tracking_no = ?", (tracking_no,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_logistics_by_order(self, order_id: str) -> dict | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM logistics WHERE order_id = ?", (order_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def search_knowledge(self, query: str, top_k: int = 3) -> list[dict]:
        """知识库检索入口：供 KnowledgeTool 调用（委托 KnowledgeIndex 实现）。"""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT category, title, content, keywords FROM knowledge"
            ).fetchall()
        return KnowledgeIndex._score(query, [self._row_to_dict(r) for r in rows], top_k=top_k)

    def list_knowledge(self) -> list[dict]:
        """列出所有知识文档。"""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT id, category, title, content, keywords FROM knowledge ORDER BY id"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_knowledge_by_id(self, kid: int) -> dict | None:
        """根据 ID 获取单条知识文档。"""
        with self._read() as conn:
            row = conn.execute(
                "SELECT id, category, title, content, keywords FROM knowledge WHERE id = ?",
                (kid,),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def create_knowledge(self, category: str, title: str, content: str, keywords: list[str] | None = None) -> dict:
        """新建知识文档（写连接短命使用）。"""
        kw_json = json.dumps(keywords or [], ensure_ascii=False)
        conn = self._connect(read_only=False)
        try:
            cur = conn.execute(
                "INSERT INTO knowledge (category, title, content, keywords) VALUES (?, ?, ?, ?)",
                (category, title, content, kw_json),
            )
            conn.commit()
            kid = cur.lastrowid
        finally:
            conn.close()
        return self.get_knowledge_by_id(kid)  # type: ignore[return-value]

    def update_knowledge(self, kid: int, category: str | None = None, title: str | None = None,
                         content: str | None = None, keywords: list[str] | None = None) -> dict | None:
        """更新知识文档，只传入需要更新的字段。"""
        existing = self.get_knowledge_by_id(kid)
        if not existing:
            return None
        updates: list[str] = []
        params: list = []
        if category is not None:
            updates.append("category = ?")
            params.append(category)
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if keywords is not None:
            updates.append("keywords = ?")
            params.append(json.dumps(keywords, ensure_ascii=False))
        if not updates:
            return existing
        params.append(kid)
        conn = self._connect(read_only=False)
        try:
            conn.execute(
                f"UPDATE knowledge SET {', '.join(updates)} WHERE id = ?", params
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_knowledge_by_id(kid)

    def delete_knowledge(self, kid: int) -> bool:
        """删除知识文档。"""
        conn = self._connect(read_only=False)
        try:
            cur = conn.execute("DELETE FROM knowledge WHERE id = ?", (kid,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def count_orders(self) -> int:
        with self._read() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for col in ("items", "shipping_address", "trajectory"):
            if d.get(col):
                try:
                    d[col] = json.loads(d[col])
                except json.JSONDecodeError:
                    pass
        if d.get("keywords"):
            try:
                d["keywords"] = json.loads(d["keywords"])
            except json.JSONDecodeError:
                pass
        return d


class KnowledgeIndex:
    """基于关键词打分的知识库检索（零外部依赖的可运行实现）。

    候选词来自知识条目 keywords 与 title；命中词越多得分越高。
    课程 23-24 讲升级为 RAG 后，可替换为向量检索实现，接口保持一致。
    """

    CRITICAL_TERMS: dict[str, list[str]] = {
        "退货": ["退货"],
        "退款": ["退款"],
        "换货": ["换货"],
        "客服电话": ["电话", "400"],
        "预约安装": ["安装", "预约"],
        "售后政策": ["售后", "保修"],
    }

    @staticmethod
    def _score(query: str, docs: list[dict], top_k: int = 3) -> list[dict]:
        scored = []
        for d in docs:
            keywords = [str(k).lower() for k in d.get("keywords", [])]
            title = str(d.get("title", ""))
            score = sum(
                1 for kw in keywords
                if kw and (query.lower() in str(d["content"]).lower() or kw in query.lower())
            )
            score += sum(1 for kw in keywords if kw in query.lower())
            if query.lower() in title.lower():
                score += 1
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"rank": i + 1, **d} for i, (_, d) in enumerate(scored[:top_k])]


def load_seeds(data_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """从 tests/test_data 加载种子数据。"""
    orders: list[dict] = []
    logistics: list[dict] = []
    knowledge_dir = data_dir / "knowledge-base"
    for name in ("orders", "logistics"):
        fp = data_dir / f"{name}.json"
        if fp.exists():
            with fp.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if name == "orders":
                orders = data
            else:
                logistics = data
    knowledge: list[dict] = []
    if knowledge_dir.exists():
        for fp in sorted(knowledge_dir.glob("*.json")):
            with fp.open("r", encoding="utf-8") as fh:
                knowledge.extend(json.load(fh))
    return orders, logistics, knowledge