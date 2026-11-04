"""失败样本候选池与版本化回归集的单元测试（006 R05 / V12 口径）。

- 未复核/重复失败只留候选，批准样本才进入新版本回归集；
- 固定基准集不被覆盖（本测试不触碰 tickets.json）；
- 回归集按版本不可变递增，新旧数据集按哈希分组。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regression_pool import (  # noqa: E402
    add_candidate,
    candidate_key,
    load_set,
    promote,
    review_candidate,
)

DATASET_A = "a" * 64
DATASET_B = "b" * 64


def _add(tmp: Path, task_id="T100", run_id="run-x", dataset=DATASET_A,
         category="business_failed") -> dict:
    return add_candidate(
        tmp, task_id=task_id, run_id=run_id, dataset_sha256=dataset,
        question_digest="q" * 16, failure_category=category,
        evidence_path="out/evidence/example",
        length_bucket="≤50",
    )


class RegressionPoolTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pool = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_and_dedupe(self):
        first = _add(self.pool)
        self.assertEqual(first["status"], "added")
        again = _add(self.pool)
        self.assertEqual(again["status"], "duplicate",
                         "同键候选不得重复入库")
        self.assertEqual(again["candidate"]["candidate_key"],
                         first["candidate"]["candidate_key"])

    def test_unreviewed_never_promoted(self):
        _add(self.pool)
        result = promote(self.pool, DATASET_A)
        self.assertEqual(result["status"], "empty")
        self.assertIsNone(load_set(self.pool))

    def test_review_then_promote_versioned(self):
        added = _add(self.pool)
        key = added["candidate"]["candidate_key"]
        reviewed = review_candidate(self.pool, key, "approved", "reviewer-1",
                                    "复现实证成立")
        self.assertEqual(reviewed["status"], "reviewed")
        promoted = promote(self.pool, DATASET_A)
        self.assertEqual(promoted["status"], "promoted")
        self.assertEqual(promoted["version"], 1)
        regression = load_set(self.pool)
        self.assertEqual(len(regression["samples"]), 1)
        self.assertEqual(regression["samples"][0]["task_id"], "T100")
        self.assertEqual(regression["dataset_sha256"], DATASET_A)
        # 再次晋升产生新版本，不改写旧版本
        _add(self.pool, task_id="T200", run_id="run-y")
        second = _add(self.pool, task_id="T300", run_id="run-z")
        review_candidate(self.pool, second["candidate"]["candidate_key"],
                         "approved", "reviewer-1", "复现实证成立")
        promoted2 = promote(self.pool, DATASET_A)
        self.assertEqual(promoted2["version"], 2)
        self.assertEqual(load_set(self.pool, 1)["version"], 1,
                         "旧版本回归集不可变")

    def test_dataset_hash_groups_sets(self):
        added = _add(self.pool, dataset=DATASET_B)
        review_candidate(self.pool, added["candidate"]["candidate_key"],
                         "approved", "reviewer-1")
        result = promote(self.pool, DATASET_A,
                         operator="op")  # 批准的是 B 数据集候选
        self.assertEqual(result["status"], "empty",
                         "候选按数据集哈希分组，不能跨集混入")

    def test_rejected_stays_out(self):
        added = _add(self.pool)
        review_candidate(self.pool, added["candidate"]["candidate_key"],
                         "rejected", "reviewer-1", "环境因素，非确定性缺陷")
        self.assertEqual(promote(self.pool, DATASET_A)["status"], "empty")

    def test_double_review_blocked(self):
        added = _add(self.pool)
        key = added["candidate"]["candidate_key"]
        review_candidate(self.pool, key, "approved", "reviewer-1")
        again = review_candidate(self.pool, key, "rejected", "reviewer-2")
        self.assertEqual(again["status"], "already_reviewed")


if __name__ == "__main__":
    unittest.main()
