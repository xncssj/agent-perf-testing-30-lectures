"""检查点生命周期单元测试：成功终态清理、失败保留与节流 GC（课程正文对应段落）。

覆盖口径：
- completed 终态检查点即时清理（默认策略），失败终态保留供续跑；
- purge_on_completed=false 时 completed 后保留；
- 存档节流触发 GC 后按总量上限淘汰最旧遗留检查点。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402

QUERY = "我的订单 2024010100002 物流到哪里了？"


def make_service(**overrides) -> WorkflowService:
    import os

    os.environ.setdefault("FLOW_DB_PATH", "/tmp/harness_m3/unit.db")
    settings = Settings()
    for k, v in overrides.items():
        setattr(settings, k, v)
    return WorkflowService(settings)


def inject_fault(service: WorkflowService) -> None:
    """response_generation 注入瞬态超时且不重试：失败后锚点停在 data_retrieval。"""
    s = service.settings
    s.fault_enabled = True
    s.fault_node_id = "response_generation"
    s.fault_mode = "timeout"
    s.fault_latency_ms = 30
    s.fault_limit = 0
    s.max_retries = 0


def heal(service: WorkflowService) -> None:
    service.settings.fault_enabled = False


class TestCheckpointLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_life_"))
        self.service = make_service(
            checkpoint_store="sqlite",
            checkpoint_db_path=self.tmp / "ckpt.db",
        )

    def tearDown(self):
        try:
            asyncio.run(self.service.aclose())
        except Exception:  # noqa: BLE001  收尾失败不影响结论
            pass

    def _run(self, execution_id=None):
        return self.service.process(QUERY, execution_id=execution_id)

    def test_completed_purges_checkpoint(self):
        result = self._run("life-ok-1")
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(
            self.service.executor.checkpoints.load_latest("life-ok-1"),
            "completed 终态后检查点应被即时清理",
        )

    def test_failed_keeps_then_resume_completes_and_purges(self):
        inject_fault(self.service)
        failed = self._run("life-9-1")
        self.assertEqual(failed["status"], "failed")
        cp = self.service.executor.checkpoints.load_latest("life-9-1")
        self.assertIsNotNone(cp, "失败终态应保留检查点供续跑")
        self.assertEqual(cp.completed_node_id, "data_retrieval")

        heal(self.service)
        resumed = self._run("life-9-1")
        self.assertEqual(resumed["status"], "completed")
        recs = resumed.get("node_executions") or []
        intent = sum(1 for r in recs if r.get("node_id") == "intent_recognition")
        data = sum(1 for r in recs if r.get("node_id") == "data_retrieval")
        self.assertEqual((intent, data), (1, 1), "续跑不应重复执行已完成节点")
        self.assertIsNone(
            self.service.executor.checkpoints.load_latest("life-9-1"),
            "续跑成功后检查点同样进入清理",
        )

    def test_purge_disabled_keeps_after_completed(self):
        self.service.settings.checkpoint_purge_on_completed = False
        result = self._run("life-keep-1")
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(
            self.service.executor.checkpoints.load_latest("life-keep-1"),
            "purge_on_completed=false 时 completed 后保留检查点",
        )

    def test_gc_throttle_trims_to_count_limit(self):
        # 每次存档都触发 GC（every=1），总量上限 2，年龄回收关闭
        self.service.settings.checkpoint_gc_every_saves = 1
        self.service.settings.checkpoint_gc_max_count = 2
        self.service.settings.checkpoint_gc_max_age_s = 0
        inject_fault(self.service)
        for i in range(4):
            result = self._run(f"life-gc-{i}")
            self.assertEqual(result["status"], "failed")
        store = self.service.executor.checkpoints
        for purged in ("life-gc-0", "life-gc-1"):
            self.assertIsNone(store.load_latest(purged), f"{purged} 应被 GC 淘汰")
        for kept in ("life-gc-2", "life-gc-3"):
            self.assertIsNotNone(store.load_latest(kept), f"{kept} 应作为最新保留")


if __name__ == "__main__":
    unittest.main()
