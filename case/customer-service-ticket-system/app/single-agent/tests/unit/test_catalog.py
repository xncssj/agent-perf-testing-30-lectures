"""工作台目录接口单元测试：tickets.json 三态加载（档目录 / 回落 / 双缺失）与 source 如实标注。

2026-10-14 实测背景：切档 overlay 把 AGENT_TEST_DATA_DIR 指向数据档而档内无
tickets.json 时，目录接口曾静默返回空列表且 source 标签硬编码，负载预览
6 列全 0 且页面无任何缺失提示。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.api.catalog import Catalog


def _write_tickets(directory: Path, count: int = 2) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "ticket_id": f"T{i:05d}",
            "user_id": f"U{i:05d}",
            "user_query": f"问题{i}",
            "order_id": f"202401010000{i}",
            "difficulty": "easy",
            "expected_branch": "auto_reply",
        }
        for i in range(1, count + 1)
    ]
    (directory / "tickets.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _catalog(data_dir: Path, fallback_dir: Path) -> Catalog:
    settings = SimpleNamespace(
        data_dir=data_dir,
        fallback_test_data_dir=fallback_dir,
        db_path=data_dir / "agent.db",
    )
    return Catalog(settings, db=None)


class CatalogTicketsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "tier"
        self.fallback_dir = self.root / "base"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_primary_dir_tickets_loaded(self) -> None:
        _write_tickets(self.data_dir)
        catalog = _catalog(self.data_dir, self.fallback_dir)
        payload = catalog.tickets()
        self.assertTrue(payload["tickets_loaded"])
        self.assertEqual(payload["source"], "tickets.json")
        self.assertEqual(payload["tickets_dir"], str(self.data_dir))
        self.assertEqual(len(payload["items"]), 2)
        conv = catalog.conversations()
        self.assertEqual(conv["source"], "tickets.json+runs")

    def test_missing_in_primary_falls_back_to_base(self) -> None:
        _write_tickets(self.fallback_dir)
        catalog = _catalog(self.data_dir, self.fallback_dir)
        payload = catalog.tickets()
        self.assertTrue(payload["tickets_loaded"])
        self.assertEqual(payload["source"], "tickets.json（回落共享基准）")
        self.assertEqual(payload["tickets_dir"], str(self.fallback_dir))
        self.assertEqual(len(payload["items"]), 2)
        conv = catalog.conversations()
        self.assertEqual(conv["source"], "tickets.json（回落共享基准）+runs")

    def test_missing_everywhere_reports_unloaded(self) -> None:
        catalog = _catalog(self.data_dir, self.fallback_dir)
        payload = catalog.tickets()
        self.assertFalse(payload["tickets_loaded"])
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["source"], "tickets.json 缺失（看板/客户库为空）")
        self.assertIn(str(self.data_dir), payload["tickets_dir"])
        self.assertIn(str(self.fallback_dir), payload["tickets_dir"])
        conv = catalog.conversations()
        self.assertEqual(conv["source"], "tickets.json 缺失（看板/客户库为空）")

    def test_customers_share_load_state(self) -> None:
        _write_tickets(self.data_dir)
        catalog = _catalog(self.data_dir, self.fallback_dir)
        payload = catalog.customers()
        self.assertTrue(payload["tickets_loaded"])
        self.assertEqual(payload["source"], "tickets.json")


if __name__ == "__main__":
    unittest.main()
