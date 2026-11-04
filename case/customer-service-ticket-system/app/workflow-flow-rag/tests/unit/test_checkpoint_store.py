"""检查点存储单元测试：内存/SQLite 语义一致性、持久化边界与生命周期。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.workflow.checkpoint_store import (  # noqa: E402
    MemoryCheckpointStore,
    SqliteCheckpointStore,
    build_checkpoint_store,
)
from src.workflow.engine import Checkpoint  # noqa: E402


def make_cp(execution_id: str, node_id: str = "data_retrieval",
            created_at: str = "2026-09-13T20:00:00") -> Checkpoint:
    # 快照含运行时对象（_scope）以验证剔除逻辑
    return Checkpoint(
        execution_id=execution_id,
        completed_node_id=node_id,
        context_snapshot={
            "query": "我的订单 2024010100002 物流到哪里了？",
            "intent": "logistics_query",
            "path": ["intent_recognition", node_id],
            "_scope": {"recorder": object(), "budget": object()},  # 不可序列化
        },
        created_at=created_at,
    )


class TestMemoryStore(unittest.TestCase):
    def test_save_and_load(self):
        store = MemoryCheckpointStore()
        store.save(make_cp("e1"))
        cp = store.load_latest("e1")
        self.assertIsNotNone(cp)
        self.assertEqual(cp.completed_node_id, "data_retrieval")
        self.assertEqual(store.backend, "memory")

    def test_new_instance_is_empty(self):
        MemoryCheckpointStore().save(make_cp("e2"))
        self.assertIsNone(MemoryCheckpointStore().load_latest("e2"))

    def test_delete(self):
        store = MemoryCheckpointStore()
        store.save(make_cp("d1"))
        self.assertTrue(store.delete("d1"))
        self.assertIsNone(store.load_latest("d1"))
        self.assertFalse(store.delete("d1"))  # 再次删除返回不存在

    def test_gc_by_age(self):
        store = MemoryCheckpointStore()
        now = datetime(2026, 9, 24, 12, 0, 0)
        store.save(make_cp("old", created_at=(now - timedelta(hours=3)).isoformat()))
        store.save(make_cp("fresh", created_at=now.isoformat()))
        purged = store.gc(max_age_s=3600, now=now)
        self.assertEqual(purged, 1)
        self.assertIsNone(store.load_latest("old"))
        self.assertIsNotNone(store.load_latest("fresh"))

    def test_gc_by_count_keeps_newest(self):
        store = MemoryCheckpointStore()
        for i in range(5):
            store.save(make_cp(f"c{i}", created_at=f"2026-09-24T10:00:0{i}"))
        purged = store.gc(max_count=2)
        self.assertEqual(purged, 3)
        self.assertIsNone(store.load_latest("c0"))
        self.assertIsNone(store.load_latest("c2"))
        self.assertIsNotNone(store.load_latest("c3"))
        self.assertIsNotNone(store.load_latest("c4"))

    def test_gc_noop_without_policy(self):
        store = MemoryCheckpointStore()
        store.save(make_cp("n1"))
        self.assertEqual(store.gc(), 0)
        self.assertIsNotNone(store.load_latest("n1"))


class TestSqliteStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_unit_"))
        self.db = self.tmp / "ckpt.db"

    def test_persists_across_instances(self):
        SqliteCheckpointStore(self.db).save(make_cp("e3"))
        # 新实例（等价进程重启）仍能读到
        cp = SqliteCheckpointStore(self.db).load_latest("e3")
        self.assertIsNotNone(cp)
        self.assertEqual(cp.completed_node_id, "data_retrieval")
        self.assertEqual(cp.context_snapshot["intent"], "logistics_query")

    def test_volatile_keys_stripped(self):
        SqliteCheckpointStore(self.db).save(make_cp("e4"))
        cp = SqliteCheckpointStore(self.db).load_latest("e4")
        self.assertNotIn("_scope", cp.context_snapshot)

    def test_latest_wins(self):
        store = SqliteCheckpointStore(self.db)
        store.save(make_cp("e5", "intent_recognition"))
        store.save(make_cp("e5", "confidence_check"))
        self.assertEqual(store.load_latest("e5").completed_node_id, "confidence_check")

    def test_missing_returns_none(self):
        self.assertIsNone(SqliteCheckpointStore(self.db).load_latest("nope"))

    def test_backend_name(self):
        self.assertEqual(SqliteCheckpointStore(self.db).backend, "sqlite")

    def test_delete(self):
        store = SqliteCheckpointStore(self.db)
        store.save(make_cp("d2"))
        self.assertTrue(store.delete("d2"))
        self.assertIsNone(store.load_latest("d2"))
        self.assertFalse(store.delete("d2"))

    def test_delete_survives_new_instance(self):
        store = SqliteCheckpointStore(self.db)
        store.save(make_cp("d3"))
        store.delete("d3")
        self.assertIsNone(SqliteCheckpointStore(self.db).load_latest("d3"))

    def test_gc_by_age(self):
        store = SqliteCheckpointStore(self.db)
        now = datetime(2026, 9, 24, 12, 0, 0)
        store.save(make_cp("old", created_at=(now - timedelta(hours=3)).isoformat()))
        store.save(make_cp("fresh", created_at=now.isoformat()))
        purged = store.gc(max_age_s=3600, now=now)
        self.assertEqual(purged, 1)
        self.assertIsNone(store.load_latest("old"))
        self.assertIsNotNone(store.load_latest("fresh"))

    def test_gc_by_count_keeps_newest(self):
        store = SqliteCheckpointStore(self.db)
        for i in range(5):
            store.save(make_cp(f"c{i}", created_at=f"2026-09-24T10:00:0{i}"))
        purged = store.gc(max_count=2)
        self.assertEqual(purged, 3)
        self.assertIsNone(store.load_latest("c0"))
        self.assertIsNotNone(store.load_latest("c4"))
        self.assertIsNotNone(store.load_latest("c3"))

    def test_gc_consistent_between_backends(self):
        """同一批检查点在两种后端按总量回收后存活集合一致。

        时间并列（k1/k2 同分钟）落在淘汰边界：两种后端都必须保留
        execution_id 字典序较大者 k2，验证并列淘汰口径一致。
        """
        now = datetime(2026, 9, 24, 12, 0, 0)
        stamps = [now - timedelta(minutes=m) for m in (40, 30, 30, 10)]
        mem, sql = MemoryCheckpointStore(), SqliteCheckpointStore(self.db)
        for i, ts in enumerate(stamps):
            mem.save(make_cp(f"k{i}", created_at=ts.isoformat()))
            sql.save(make_cp(f"k{i}", created_at=ts.isoformat()))
        self.assertEqual(mem.gc(max_count=2), 2)
        self.assertEqual(sql.gc(max_count=2), 2)
        alive_mem = {e for e in ("k0", "k1", "k2", "k3") if mem.load_latest(e)}
        alive_sql = {e for e in ("k0", "k1", "k2", "k3") if sql.load_latest(e)}
        self.assertEqual(alive_mem, {"k2", "k3"})
        self.assertEqual(alive_mem, alive_sql)


class TestBuildStore(unittest.TestCase):
    def test_build_memory_and_sqlite(self):
        class S:
            checkpoint_store = "memory"
            checkpoint_db_path = Path(tempfile.mkdtemp()) / "x.db"

        self.assertEqual(build_checkpoint_store(S()).backend, "memory")
        S.checkpoint_store = "sqlite"
        self.assertEqual(build_checkpoint_store(S()).backend, "sqlite")

    def test_unknown_kind_fails_fast(self):
        class S:
            checkpoint_store = "redis"
            checkpoint_db_path = Path("/tmp/x.db")

        with self.assertRaises(ValueError):
            build_checkpoint_store(S())


if __name__ == "__main__":
    unittest.main()
