"""检查点存储：内存与 SQLite 持久化两种实现，含生命周期（删除与回收）。

设计约束（课程正文对应段落）：
- 快照中 `_` 前缀键（当前为 `_scope`，承载 recorder/budget/tool_executor
  等运行时对象）不属于业务状态：序列化前剔除，恢复时由
  `execute(scope=...)` 重新注入。因此持久化不破坏续跑语义。
- 两种实现共用同一接口：`save(cp)` / `load_latest(execution_id)` /
  `delete(execution_id)` / `gc(max_age_s, max_count)` / `backend`，
  与既有内存实现行为一致（每个 execution_id 只保留最新一条）。
- 生命周期口径：检查点只为"未到成功终态的运行"保留续跑能力——
  `delete` 供运行器在成功终态后即时清理；`gc` 按存活时长与总量上限
  回收遗留检查点（如故障后从未续跑的执行），两种后端策略一致：
  先按 `created_at` 淘汰超期项，再按 `created_at` 新序保留最新 max_count 条，
  时间并列时按 `execution_id` 字典序保证确定性。
- 持久化实现按调用创建连接并启用 WAL：避免闭包共享连接在并发下产生
  竞态（案例一容量测试的既有教训），代价是每次存取一次连接开销。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

VOLATILE_PREFIX = "_"


def _strip_volatile(context: dict) -> dict:
    """剔除运行时对象（`_` 前缀键），保证快照可序列化。"""
    return {k: v for k, v in (context or {}).items()
            if not str(k).startswith(VOLATILE_PREFIX)}


class MemoryCheckpointStore:
    """进程内检查点存储（默认；进程重启即丢失）。"""

    backend = "memory"

    def __init__(self) -> None:
        self._items: dict = {}

    def save(self, cp) -> None:
        self._items[cp.execution_id] = cp

    def load_latest(self, execution_id: str):
        return self._items.get(execution_id)

    def delete(self, execution_id: str) -> bool:
        """删除指定执行的检查点；返回是否存在过该条目。"""
        return self._items.pop(execution_id, None) is not None

    def gc(self, max_age_s: float | None = None, max_count: int | None = None,
           now: datetime | None = None) -> int:
        """按存活时长与总量上限回收；返回回收条数。两项均 None 时不回收。"""
        if max_age_s is None and max_count is None:
            return 0
        kept = dict(self._items)
        if max_age_s is not None:
            cutoff = (now or datetime.now()) - timedelta(seconds=float(max_age_s))
            for eid, cp in list(kept.items()):
                try:
                    created = datetime.fromisoformat(str(cp.created_at))
                except ValueError:
                    continue  # 时间不可解析的条目不参与年龄回收
                if created < cutoff:
                    del kept[eid]
        if max_count is not None and len(kept) > int(max_count):
            # 时间新序保留最新 N 条；并列时保留 execution_id 字典序较大者，
            # 与 SQLite 后端 ORDER BY ... DESC, DESC 的淘汰结果一致
            ordered = sorted(
                kept.items(),
                key=lambda kv: (str(kv[1].created_at), kv[0]),
                reverse=True,
            )
            kept = dict(ordered[: int(max_count)])
        purged = len(self._items) - len(kept)
        self._items = kept
        return purged


class SqliteCheckpointStore:
    """SQLite 持久化检查点存储（跨进程重启可续跑）。"""

    backend = "sqlite"

    def __init__(self, db_path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    execution_id      TEXT PRIMARY KEY,
                    completed_node_id TEXT NOT NULL,
                    context_snapshot  TEXT NOT NULL,
                    created_at        TEXT NOT NULL
                )
                """
            )

    def save(self, cp) -> None:
        from .engine import Checkpoint

        snapshot = _strip_volatile(cp.context_snapshot)
        payload = json.dumps(snapshot, ensure_ascii=False, default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO checkpoints "
                "(execution_id, completed_node_id, context_snapshot, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(execution_id) DO UPDATE SET "
                "completed_node_id=excluded.completed_node_id, "
                "context_snapshot=excluded.context_snapshot, "
                "created_at=excluded.created_at",
                (cp.execution_id, cp.completed_node_id, payload, cp.created_at),
            )

    def load_latest(self, execution_id: str):
        from .engine import Checkpoint

        with self._connect() as conn:
            row = conn.execute(
                "SELECT completed_node_id, context_snapshot, created_at "
                "FROM checkpoints WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        if row is None:
            return None
        completed_node_id, snapshot, created_at = row
        return Checkpoint(
            execution_id=execution_id,
            completed_node_id=completed_node_id,
            context_snapshot=json.loads(snapshot),
            created_at=created_at,
        )

    def delete(self, execution_id: str) -> bool:
        """删除指定执行的检查点；返回是否存在过该条目。"""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM checkpoints WHERE execution_id = ?",
                (execution_id,),
            )
        return cur.rowcount > 0

    def gc(self, max_age_s: float | None = None, max_count: int | None = None,
           now: datetime | None = None) -> int:
        """按存活时长与总量上限回收；返回回收条数。两项均 None 时不回收。

        年龄判定用 sqlite 的 datetime() 归一化比较（秒级精度，足够回收粒度）；
        无法解析的时间不参与年龄淘汰。总量淘汰按 created_at 新序保留
        最新 N 条，时间并列保留 execution_id 字典序较大者，与内存后端一致。
        """
        if max_age_s is None and max_count is None:
            return 0
        purged = 0
        with self._connect() as conn:
            if max_age_s is not None:
                cutoff = (now or datetime.now()) - timedelta(seconds=float(max_age_s))
                cur = conn.execute(
                    "DELETE FROM checkpoints WHERE datetime(created_at) < datetime(?)",
                    (cutoff.isoformat(sep=" "),),
                )
                purged += cur.rowcount
            if max_count is not None:
                cur = conn.execute(
                    "DELETE FROM checkpoints WHERE execution_id NOT IN ("
                    "SELECT execution_id FROM checkpoints "
                    "ORDER BY created_at DESC, execution_id DESC LIMIT ?)",
                    (int(max_count),),
                )
                purged += cur.rowcount
        return purged


def build_checkpoint_store(settings):
    """按配置构建检查点存储；未知取值 fail fast。"""
    kind = (getattr(settings, "checkpoint_store", "memory") or "memory").lower()
    if kind == "memory":
        return MemoryCheckpointStore()
    if kind == "sqlite":
        return SqliteCheckpointStore(getattr(settings, "checkpoint_db_path"))
    raise ValueError(f"未知 checkpoint.store 取值: {kind!r}（可选 memory / sqlite）")
