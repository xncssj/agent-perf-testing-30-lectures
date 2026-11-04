"""SQLite 数据访问层：订单与物流快照、知识库语料的加载与查询。

压测前由 scripts/init_db.py 或服务启动时导入 tests/test_data 的快照，
运行期间数据保持不变，确保基线可复现。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any


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


MYSQL_KNOWLEDGE_SQL = """
CREATE TABLE IF NOT EXISTS knowledge (
    id BIGINT NOT NULL AUTO_INCREMENT,
    category VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
MYSQL_ORDERS_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    order_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64),
    status VARCHAR(32),
    items TEXT,
    total_amount DOUBLE,
    shipping_address TEXT,
    tracking_no VARCHAR(64),
    carrier VARCHAR(32),
    create_time VARCHAR(40),
    update_time VARCHAR(40),
    PRIMARY KEY (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
MYSQL_LOGISTICS_SQL = """
CREATE TABLE IF NOT EXISTS logistics (
    tracking_no VARCHAR(64) NOT NULL,
    order_id VARCHAR(64),
    carrier VARCHAR(32),
    status VARCHAR(32),
    current_location VARCHAR(255),
    origin VARCHAR(255),
    destination VARCHAR(255),
    trajectory TEXT,
    estimated_delivery VARCHAR(64),
    PRIMARY KEY (tracking_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
MYSQL_ADMIN_USERS_SQL = """
CREATE TABLE IF NOT EXISTS admin_users (
    id BIGINT NOT NULL AUTO_INCREMENT,
    username VARCHAR(64) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(64) NOT NULL DEFAULT '',
    email VARCHAR(128) NOT NULL DEFAULT '',
    phone VARCHAR(32) NOT NULL DEFAULT '',
    title VARCHAR(64) NOT NULL DEFAULT '',
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_admin_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""
MYSQL_ADMIN_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS admin_sessions (
    token VARCHAR(128) NOT NULL,
    user_id BIGINT NOT NULL,
    expires_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (token),
    CONSTRAINT fk_admin_sessions_user FOREIGN KEY (user_id) REFERENCES admin_users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class _SqliteConn:
    """保持 sqlite3 连接的 execute / executescript 形态。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple | list = ()):
        return self._conn.execute(sql, params)

    def executescript(self, script: str) -> None:
        self._conn.executescript(script)

    def commit(self) -> None:
        self._conn.commit()


class _MysqlConn:
    """把 SQLite 风格的 ? 占位符转成 PyMySQL 的 %s。

    并发修复（见课程案例的根因分析 §3）：PyMySQL 连接
    非线程安全，多线程共享同一条连接时报文交错，协议流一次性永久损坏
    （InterfaceError(0,'') 且黏性不可自愈）。改为线程本地连接：每线程惰性
    建立独享连接；execute 遇连接级错误（服务端关闭/协议损坏/网络中断）时
    弃旧重建并重试一次。execute/commit 接口与共享连接时代完全一致。
    """

    def __init__(self, factory) -> None:
        self._factory = factory
        self._local = threading.local()

    def _connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._factory()
            self._local.conn = conn
        return conn

    @staticmethod
    def _is_connect_error(exc: Exception) -> bool:
        import pymysql

        if isinstance(exc, pymysql.err.InterfaceError):
            return True
        args = getattr(exc, "args", None)
        code = args[0] if args else None
        # 2002/2003=无法建立连接，2013=通信中途丢失
        return code in (2002, 2003, 2013)

    def _discard(self, conn) -> None:
        try:
            conn.close()
        except Exception:
            pass
        if getattr(self._local, "conn", None) is conn:
            self._local.conn = None

    def execute(self, sql: str, params: tuple | list = ()):
        statement = sql.replace("?", "%s")
        conn = self._connection()
        try:
            cursor = conn.cursor()
            cursor.execute(statement, params or None)
            return cursor
        except Exception as exc:
            if not self._is_connect_error(exc):
                raise
            self._discard(conn)
            cursor = self._connection().cursor()
            cursor.execute(statement, params or None)
            return cursor

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            text = stmt.strip()
            if text:
                self.execute(text)

    def commit(self) -> None:
        self._connection().commit()


def _mysql_database_name(cfg: dict) -> str:
    return str(cfg["database"]).replace("`", "")


def _mysql_retry_connect(build, *, what: str, host: str, port: int):
    """容器启动竞态只重试连接拒绝/中断类错误，其余原样抛出。"""
    import pymysql

    last: Exception | None = None
    for _ in range(30):
        try:
            return build()
        except Exception as exc:
            code = exc.args[0] if getattr(exc, "args", None) else None
            if code not in (2002, 2003, 2013):
                raise
            last = exc
            time.sleep(2)
    raise RuntimeError(f"无法连接 MySQL {host}:{port}（{what}）") from last


def _mysql_bootstrap_database(cfg: dict) -> None:
    """库不存在时先创建（幂等，进程内只调一次）。"""
    import pymysql

    def build():
        return pymysql.connect(
            host=cfg["host"],
            port=int(cfg["port"]),
            user=cfg["user"],
            password=cfg["password"],
            charset="utf8mb4",
            autocommit=True,
        )

    conn = _mysql_retry_connect(build, what="建库", host=cfg["host"], port=int(cfg["port"]))
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{_mysql_database_name(cfg)}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


def _new_mysql_connection(cfg: dict):
    """新建一条运行连接（线程本地惰性建连的工厂）。"""
    import pymysql
    from pymysql.cursors import DictCursor

    def build():
        return pymysql.connect(
            host=cfg["host"],
            port=int(cfg["port"]),
            user=cfg["user"],
            password=cfg["password"],
            database=_mysql_database_name(cfg),
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    return _mysql_retry_connect(build, what="建连", host=cfg["host"], port=int(cfg["port"]))


class Database:
    def __init__(self, db_path: Path, mysql: dict | None = None) -> None:
        self.db_path = db_path
        self.dialect = "mysql" if mysql else "sqlite"
        if mysql:
            _mysql_bootstrap_database(mysql)
            self._conn = _MysqlConn(lambda: _new_mysql_connection(mysql))
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False：FastAPI 同步端点在线程池中执行
            raw = sqlite3.connect(str(db_path), check_same_thread=False)
            raw.row_factory = sqlite3.Row
            self._conn = _SqliteConn(raw)
        self._init_schema()
        self._knowledge_index = KnowledgeIndex(self._conn)

    def _init_schema(self) -> None:
        if self.dialect == "mysql":
            self._conn.execute(MYSQL_ORDERS_SQL)
            self._conn.execute(MYSQL_LOGISTICS_SQL)
            self._conn.execute(MYSQL_KNOWLEDGE_SQL)
            self._conn.commit()
        else:
            self._conn.execute(ORDERS_SQL)
            self._conn.execute(LOGISTICS_SQL)
            self._conn.execute(KNOWLEDGE_SQL)
            self._conn.commit()
        self._ensure_admin()

    def _ensure_admin(self) -> None:
        """建管理员表；空库写入与 sql/admin_seed.sql 相同的初始账号。"""
        from ..services.admin_auth import SEED_HASH, SEED_USERNAME, SCHEMA_SQL, utc_now_iso

        if self.dialect == "mysql":
            self._conn.execute(MYSQL_ADMIN_USERS_SQL)
            self._conn.execute(MYSQL_ADMIN_SESSIONS_SQL)
        else:
            self._conn.executescript(SCHEMA_SQL)
        row = self._conn.execute(
            "SELECT id FROM admin_users WHERE username = ?", (SEED_USERNAME,)
        ).fetchone()
        if row:
            self._conn.commit()
            return
        now = utc_now_iso()
        self._conn.execute(
            """INSERT INTO admin_users
               (username, password_hash, display_name, email, phone, title, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (SEED_USERNAME, SEED_HASH, "系统管理员", "admin@example.com", "", "系统管理员", now, now),
        )
        self._conn.commit()

    def get_admin_by_username(self, username: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None

    def get_admin_by_id(self, user_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM admin_users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_admin_profile(
        self, user_id: int, display_name: str, email: str, phone: str, title: str
    ) -> dict | None:
        from ..services.admin_auth import utc_now_iso

        self._conn.execute(
            """UPDATE admin_users
               SET display_name = ?, email = ?, phone = ?, title = ?, updated_at = ?
               WHERE id = ?""",
            (display_name, email, phone, title, utc_now_iso(), user_id),
        )
        self._conn.commit()
        return self.public_admin(self.get_admin_by_id(user_id))

    def update_admin_password(self, user_id: int, password_hash: str) -> None:
        from ..services.admin_auth import utc_now_iso

        self._conn.execute(
            "UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, utc_now_iso(), user_id),
        )
        self._conn.commit()

    def create_admin_session(self, user_id: int, token: str, expires_at: str) -> None:
        self._conn.execute(
            "INSERT INTO admin_sessions (token, user_id, expires_at) VALUES (?,?,?)",
            (token, user_id, expires_at),
        )
        self._conn.commit()

    def get_admin_by_token(self, token: str) -> dict | None:
        from ..services.admin_auth import is_expired

        row = self._conn.execute(
            """SELECT u.* FROM admin_users u
               JOIN admin_sessions s ON s.user_id = u.id
               WHERE s.token = ?""",
            (token,),
        ).fetchone()
        if not row:
            return None
        sess = self._conn.execute(
            "SELECT expires_at FROM admin_sessions WHERE token = ?", (token,)
        ).fetchone()
        if not sess or is_expired(sess["expires_at"]):
            self.delete_admin_session(token)
            return None
        return dict(row)

    def delete_admin_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
        self._conn.commit()

    @staticmethod
    def public_admin(row: dict | None) -> dict | None:
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row.get("display_name") or "",
            "email": row.get("email") or "",
            "phone": row.get("phone") or "",
            "title": row.get("title") or "",
            "updated_at": row.get("updated_at") or "",
        }

    def seed_loads(self, orders: list[dict], logistics: list[dict], knowledge: list[dict]) -> None:
        """导入快照数据（先清空再写入，保证与数据文件一致）。"""
        conn = self._conn
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

    ###########################################################################
    # 查询
    ###########################################################################
    def get_order(self, order_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_logistics_by_tracking(self, tracking_no: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM logistics WHERE tracking_no = ?", (tracking_no,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_logistics_by_order(self, order_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM logistics WHERE order_id = ?", (order_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_knowledge(self) -> list[dict]:
        """列出已导入的知识库文档（工作台知识库页使用）。"""
        rows = self._conn.execute(
            "SELECT id, category, title, content, keywords FROM knowledge ORDER BY id"
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_knowledge_by_id(self, kid: int) -> dict | None:
        """根据 ID 获取单条知识文档。"""
        row = self._conn.execute(
            "SELECT id, category, title, content, keywords FROM knowledge WHERE id = ?",
            (kid,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def create_knowledge(self, category: str, title: str, content: str, keywords: list[str] | None = None) -> dict:
        """新建知识文档。"""
        kw_json = json.dumps(keywords or [], ensure_ascii=False)
        cur = self._conn.execute(
            "INSERT INTO knowledge (category, title, content, keywords) VALUES (?, ?, ?, ?)",
            (category, title, content, kw_json),
        )
        self._conn.commit()
        kid = cur.lastrowid
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
        self._conn.execute(
            f"UPDATE knowledge SET {', '.join(updates)} WHERE id = ?", params
        )
        self._conn.commit()
        return self.get_knowledge_by_id(kid)

    def delete_knowledge(self, kid: int) -> bool:
        """删除知识文档。"""
        cur = self._conn.execute("DELETE FROM knowledge WHERE id = ?", (kid,))
        self._conn.commit()
        return cur.rowcount > 0

    def search_knowledge(self, query: str, top_k: int = 3) -> list[dict]:
        """知识库检索入口：供 KnowledgeTool 调用（委托 KnowledgeIndex 实现）。"""
        return self._knowledge_index.search(query, top_k=top_k)

    def search_knowledge_with_trace(self, query: str, top_k: int = 3) -> dict:
        """阶段三同构的完整检索返回：hits + 溯源元数据。

        契约对齐 RagEngine.search（阶段三）：doc_ids、top_k、latency_ms、
        corpus、index_version、corpus_docs；供上层溯源/可观测接入使用，
        不改变 search_knowledge 的既有返回结构。
        """
        t0 = time.perf_counter()
        hits = self.search_knowledge(query, top_k=top_k)
        latency_ms = round((time.perf_counter() - t0) * 1000, 3)
        index = self._knowledge_index
        return {
            "hits": hits,
            "doc_ids": [h.get("doc_id") for h in hits],
            "top_k": top_k,
            "latency_ms": latency_ms,
            "corpus": index._corpus,
            "index_version": index._index_version,
            "corpus_docs": index._corpus_docs,
        }

    def count_orders(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()
        return int(row["n"])

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
    """知识库检索：关键词命中 + 业务术语表 + BM25 混合打分（零外部依赖）。

    检索分三层，加权求和：
    1. 关键词命中（keywords 字段与 query 的子串匹配）——精确可解释；
    2. 业务术语表（CRITICAL_TERMS）——把口语变体（如「怎么退」）归约到
       标准术语（退货/退款），补偿口语与书面语的差异；
    3. BM25 词典计分（中文按 2-gram 切分、英文/数字按空白切分）——
       处理改写与上下文扩散，不引入分词依赖。

    对外契约保持 search(query, top_k) -> [{rank, category, title, content, keywords}]
    不变，便于课程 23-24 讲替换为向量检索实现；当前数据规模下构建/查询开销可忽略。
    """

    _K1 = 1.5
    _B = 0.75
    _KW_HIT = 3.0    # 关键词直接命中权重
    _TERM_HIT = 2.0  # 业务术语间接命中权重
    _TITLE_HIT = 2.0 # query 命中标题权重
    _BM25_W = 1.0    # BM25 得分权重

    CRITICAL_TERMS: dict[str, list[str]] = {
        "退货": ["退货"],
        "退款": ["退款"],
        "换货": ["换货"],
        "客服电话": ["电话", "400"],
        "预约安装": ["安装", "预约"],
        "售后政策": ["售后", "保修"],
    }

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._docs: list[dict] = []
        self._doc_tf: list[Counter] = []
        self._doc_len: list[int] = []
        self._total = 0
        self._df: dict[str, int] = {}
        self._built_rows: int | None = None
        # 溯源元数据（对齐阶段三 RagEngine 契约，见数据库设计 数据库设计说明）
        self._corpus = "customer-service-kb"
        self._corpus_docs = 0
        self._index_version = ""

    def _docs_fingerprint(self) -> str:
        """语料指纹：doc_id + 内容哈希，语料或文档变化即变化（前 8 位）。"""
        h = hashlib.sha256()
        for doc in sorted(self._docs, key=lambda x: x["doc_id"]):
            h.update(f"{doc['doc_id']}\x00{doc['content']}".encode("utf-8"))
        return h.hexdigest()[:8]

    def _rebuild(self) -> None:
        """加载全部知识条目并构建 BM25 词频索引。

        采用惰性重建：检索前比较表行数，seed_loads 或后续增量写入后自动生效。
        """
        self._docs = self._load_docs()
        self._corpus_docs = len(self._docs)
        self._index_version = self._docs_fingerprint()
        self._doc_tf = []
        self._doc_len = []
        df: Counter = Counter()
        for doc in self._docs:
            toks = self._tokens(f'{doc["title"]} {doc["content"]}')
            freq = Counter(toks)
            self._doc_tf.append(freq)
            self._doc_len.append(sum(freq.values()) or 1)
            df.update(freq.keys())
        self._total = len(self._docs)
        self._df = dict(df)

    def _load_docs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT category, title, content, keywords FROM knowledge"
        ).fetchall()
        docs = [self._row_to_dict(r) for r in rows]
        for doc in docs:
            # doc_id 对齐阶段三 DataStore.doc_id（数据库设计说明 检索质量断言口径）
            doc["doc_id"] = f"{doc['category']}:{doc['title']}"
        return docs

    @staticmethod
    def _tokens(text: str) -> list[str]:
        """词元化：连续中文按 2-gram，英文/数字按空白切分（零依赖）。"""
        chinese = re.findall(r"[\u4e00-\u9fff]+", text)
        others = re.findall(r"[A-Za-z0-9]+", text, re.ASCII)
        tokens: list[str] = [t.lower() for t in others]
        for seg in chinese:
            grams = [seg[i : i + 2] for i in range(len(seg) - 1)]
            tokens.extend(grams or [seg])
        return tokens

    def _bm25_scores(self, query: str) -> list[float]:
        """对每个文档计算 BM25 得分（词项来自 query 的同款切词）。"""
        scores = [0.0] * self._total
        query_tokens = self._tokens(query)
        if not query_tokens or not self._docs:
            return scores
        avgdl = sum(self._doc_len) / len(self._docs)
        for token in query_tokens:
            df = self._df.get(token, 0)
            if not df or df == self._total:
                continue
            idf = math.log(1.0 + (self._total - df + 0.5) / (df + 0.5))
            for i, freq in enumerate(self._doc_tf):
                tf = freq.get(token, 0)
                if not tf:
                    continue
                dl = self._doc_len[i]
                denom = tf + self._K1 * (1.0 - self._B + self._B * dl / avgdl)
                scores[i] += idf * (tf * (self._K1 + 1.0)) / denom
        return scores

    def _keyword_scores(self, query: str) -> list[float]:
        """关键词与业务术语命中分：query 口语可命中术语表，再映射到条目关键词。"""
        q = query.lower()
        terms: set[str] = set()
        weak: set[str] = set()
        for key, words in self.CRITICAL_TERMS.items():
            if key in q or any(w and w in q for w in words):
                terms.update(words)
                terms.add(key)
            elif len(key) == 2 and any(ch in q for ch in key):
                # 弱命中：2 字术语仅命中其中一字（如「怎么退」→「退货」），
                # 保证口语变体也能召回，避免纯 BM25 2-gram 跨度过大的漏检。
                weak.add(key)
        scores: list[float] = []
        for doc in self._docs:
            s = 0.0
            for kw in (str(k).lower() for k in doc.get("keywords", [])):
                if not kw:
                    continue
                if kw in q:
                    s += self._KW_HIT
                elif any(t in kw for t in terms):
                    s += self._TERM_HIT
                elif kw in weak:
                    s += self._TERM_HIT / 4
            if q and q in str(doc["title"]).lower():
                s += self._TITLE_HIT
            scores.append(s)
        return scores

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        if not query:
            return []
        # MySQL DictCursor 返回 dict，sqlite3 返回 Row：二者都支持键名访问，
        # 下标访问仅在 SQLite 侧成立（云端 MySQL 模式实测必现 KeyError(0)）
        counted = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM knowledge"
        ).fetchone()["cnt"]
        if counted != self._built_rows:
            self._rebuild()
            self._built_rows = counted
        if not self._docs:
            return []
        kw = self._keyword_scores(query)
        bm = self._bm25_scores(query)
        scored = [(kw[i] * 4 + self._BM25_W * bm[i], doc)
                  for i, doc in enumerate(self._docs)]
        scored.sort(key=lambda x: (-x[0], x[1]["title"]))
        return [
            {"rank": idx + 1, **doc}
            for idx, (_, doc) in enumerate(scored[:top_k])
        ]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        if d.get("keywords"):
            try:
                d["keywords"] = json.loads(d["keywords"])
            except json.JSONDecodeError:
                pass
        return d


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