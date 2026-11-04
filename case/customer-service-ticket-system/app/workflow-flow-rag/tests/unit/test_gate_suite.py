"""同轮评测套件与三态门禁的单元测试（006 R01 / V03—V05）。

覆盖四类门禁用例：
- 缺报告 / 清单文件缺失 → NOT_VERIFIED（MISSING_REPORT 等原因码）；
- 指纹混轮（G01：bm25 与 hybrid 混入同一轮）→ NOT_VERIFIED；
- 缺字段（G02：成本、安全违规、稳定性）→ 抛 MissingReportError，
  由 gate_suite 归入 NOT_VERIFIED，不填理想值；
- 完整证据 → 按既有七项阈值判 PASS / FAIL。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gates import suite as suite_mod  # noqa: E402
from src.gates.executor import GateExecutor  # noqa: E402
from src.gates.report_metrics import (  # noqa: E402
    MissingReportError,
    build_gate_metrics,
)

SUITE_ID = "suite-20260926-unit"
DATASET_SHA = "a" * 64
WINDOW = ("2026-09-26T10:00:00", "2026-09-26T10:10:00")


def _fingerprint(search_mode: str = "bm25") -> dict:
    return {
        "provider": "mock", "model": "mock", "architecture": "workflow+rag",
        "search_mode": search_mode, "top_k": 5, "confidence_threshold": 0.8,
        "mock_seed": 42,
    }


def _bench_payload(search_mode: str = "bm25", with_fields: bool = True) -> dict:
    agg = {
        "合格率": {"合格业务百分比": 99.0},
        "时延指标(mm)": {"E2E": {"p90": 3600.0}},
        "安全指标": {"违规条数": 0} if with_fields else {},
        "成本指标": {"成功业务成本(元/合格业务)": 0.02} if with_fields else {},
    }
    return {
        "test_type": "benchmark", "run_id": "bench-run-1",
        "started_at": WINDOW[0], "ended_at": WINDOW[1],
        "dataset_sha256": DATASET_SHA,
        "fingerprint": _fingerprint(search_mode), "aggregate": agg,
    }


def _branch_payload(search_mode: str = "bm25") -> dict:
    return {
        "test_type": "branch_coverage", "run_id": "branch-run-1",
        "started_at": WINDOW[0], "ended_at": WINDOW[1],
        "dataset_sha256": DATASET_SHA,
        "fingerprint": _fingerprint(search_mode),
        "coverage": {"branch_coverage": 1.0},
    }


def _stability_payload(search_mode: str = "bm25", with_fields: bool = True) -> dict:
    return {
        "test_type": "stability", "run_id": "stability-run-1",
        "started_at": WINDOW[0], "ended_at": WINDOW[1],
        "dataset_sha256": DATASET_SHA,
        "fingerprint": _fingerprint(search_mode),
        "availability": 1.0 if with_fields else None,
        "error_rate": 0.0 if with_fields else None,
    }


def _simple_payload(rtype: str, search_mode: str = "bm25") -> dict:
    extra = {
        "rag_quality": {"quality": {"precision_top1": 0.95}},
        "longtail": {"passed": 5, "total": 5},
        "critical_path": {"checkpoint_enabled": True},
    }[rtype]
    return {
        "test_type": rtype, "run_id": f"{rtype}-run-1",
        "started_at": WINDOW[0], "ended_at": WINDOW[1],
        "dataset_sha256": DATASET_SHA,
        "fingerprint": _fingerprint(search_mode), **extra,
    }


def _write_reports(reports_dir: Path, search_mode: str = "bm25",
                   bench_with_fields: bool = True,
                   stability_with_fields: bool = True) -> dict[str, str]:
    """向临时目录写入六类报告，返回 create_suite_manifest 需要的路径表。"""
    payloads = {
        "benchmark": _bench_payload(search_mode, bench_with_fields),
        "branch_coverage": _branch_payload(search_mode),
        "rag_quality": _simple_payload("rag_quality", search_mode),
        "longtail": _simple_payload("longtail", search_mode),
        "critical_path": _simple_payload("critical_path", search_mode),
        "stability": _stability_payload(search_mode, stability_with_fields),
    }
    paths = {}
    for rtype, payload in payloads.items():
        name = f"{rtype}_workflow_20260926.json"
        (reports_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths[rtype] = name
    return paths


class SuiteManifestTest(unittest.TestCase):
    """冻结清单建立与校验（G01 混轮防护）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.reports_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _manifest(self, reports_dir: Path) -> dict:
        paths = _write_reports(reports_dir)
        return suite_mod.create_suite_manifest(
            reports_dir, SUITE_ID, paths, dataset_sha256=DATASET_SHA,
            planned_window={"started_at": "2026-09-26T09:00:00",
                            "ended_at": "2026-09-26T11:00:00"})

    def test_create_and_validate_ok(self):
        manifest = self._manifest(self.reports_dir)
        self.assertEqual(manifest["evaluation_suite_id"], SUITE_ID)
        loaded, issues = suite_mod.validate_suite(manifest, self.reports_dir)
        self.assertEqual(issues, [])
        self.assertEqual(set(loaded), set(suite_mod.REQUIRED_REPORT_TYPES))

    def test_mixed_fingerprint_rejected_at_creation(self):
        # G01 现象：一轮里混入 hybrid 旧报告
        paths = _write_reports(self.reports_dir)
        paths["branch_coverage"] = "stale.json"
        payload = _branch_payload(search_mode="hybrid")
        (self.reports_dir / "stale.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(suite_mod.SuiteError):
            suite_mod.create_suite_manifest(
                self.reports_dir, SUITE_ID, paths, dataset_sha256=DATASET_SHA)

    def test_validate_detects_swapped_fingerprint(self):
        manifest = self._manifest(self.reports_dir)
        # 冻结后把某份报告替换成旧口径指纹
        target = self.reports_dir / manifest["reports"]["longtail"]["file"]
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["fingerprint"]["search_mode"] = "hybrid"
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _, issues = suite_mod.validate_suite(manifest, self.reports_dir)
        codes = {i["code"] for i in issues}
        self.assertIn("FINGERPRINT_MISMATCH", codes)

    def test_validate_detects_missing_report(self):
        manifest = self._manifest(self.reports_dir)
        (self.reports_dir / manifest["reports"]["stability"]["file"]).unlink()
        _, issues = suite_mod.validate_suite(manifest, self.reports_dir)
        self.assertEqual([i["code"] for i in issues], ["MISSING_REPORT"])

    def test_validate_detects_window_unverifiable(self):
        manifest = self._manifest(self.reports_dir)
        target = self.reports_dir / manifest["reports"]["benchmark"]["file"]
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload.pop("started_at")
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _, issues = suite_mod.validate_suite(manifest, self.reports_dir)
        self.assertIn("WINDOW_UNVERIFIABLE", [i["code"] for i in issues])

    def test_legacy_report_without_identity_rejected(self):
        # 旧口径报告无 run_id/窗口，不得补造身份纳入套件
        paths = _write_reports(self.reports_dir)
        stale = _bench_payload()
        stale.pop("run_id"); stale.pop("started_at"); stale.pop("ended_at")
        (self.reports_dir / "legacy_bench.json").write_text(
            json.dumps(stale, ensure_ascii=False), encoding="utf-8")
        paths["benchmark"] = "legacy_bench.json"
        with self.assertRaises(suite_mod.SuiteError):
            suite_mod.create_suite_manifest(
                self.reports_dir, SUITE_ID, paths, dataset_sha256=DATASET_SHA)


class GateMetricsStrictnessTest(unittest.TestCase):
    """缺字段不填理想值（G02 / V03）。"""

    def setUp(self):
        self.bench = _bench_payload()
        self.branch = _branch_payload()
        self.stability = _stability_payload()

    def test_complete_metrics(self):
        metrics = build_gate_metrics(self.bench, self.branch, self.stability)
        self.assertEqual(metrics["security_violations"], 0)
        self.assertEqual(metrics["availability"], 1.0)
        self.assertEqual(metrics["cost_per_business"], 0.02)

    def test_missing_stability_report_raises(self):
        with self.assertRaises(MissingReportError):
            build_gate_metrics(self.bench, self.branch, None)

    def test_missing_stability_fields_raise(self):
        with self.assertRaises(MissingReportError):
            build_gate_metrics(self.bench, self.branch,
                               _stability_payload(with_fields=False))

    def test_missing_security_field_raises(self):
        with self.assertRaises(MissingReportError):
            build_gate_metrics(_bench_payload(with_fields=False),
                               self.branch, self.stability)

    def test_missing_cost_field_raises(self):
        bench = _bench_payload(with_fields=False)
        with self.assertRaises(MissingReportError):
            build_gate_metrics(bench, self.branch, self.stability)


class ExecutorThreeStateTest(unittest.TestCase):
    """三态判定：缺字段 NOT_VERIFIED，完整证据按真实阈值 PASS/FAIL（V03/V05）。"""

    def setUp(self):
        self.executor = GateExecutor({
            "accuracy": {"threshold": 0.98},
            "performance": {"threshold": 8000},
            "coverage": {"threshold": 0.90},
            "security": {"threshold": 0},
            "cost_per_qualified": {"threshold": 0.5},
            "stability": {"threshold": 0.995, "error_rate_max": 0.005},
        })

    def _full_report(self, **overrides) -> dict:
        metrics = build_gate_metrics(
            _bench_payload(), _branch_payload(), _stability_payload())
        metrics["_provenance"] = {
            "qualified_rate": "benchmark", "e2e_p90_ms": "benchmark",
            "security_violations": "benchmark", "cost_per_business": "benchmark",
            "branch_coverage": "branch_coverage",
            "availability": "stability", "error_rate": "stability",
        }
        metrics.update(overrides)
        return metrics

    def test_pass_with_real_thresholds(self):
        result = self.executor.evaluate(self._full_report())
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["all_pass"])
        sources = {g["gate"]: g["source_report"] for g in result["gates"]}
        self.assertEqual(sources["availability"], "stability")
        self.assertEqual(sources["accuracy"], "benchmark")

    def test_fail_on_threshold_breach(self):
        result = self.executor.evaluate(self._full_report(qualified_rate=0.95))
        self.assertEqual(result["verdict"], "FAIL")
        self.assertFalse(result["all_pass"])

    def test_missing_metric_is_not_verified(self):
        report = self._full_report()
        report["branch_coverage"] = None
        result = self.executor.evaluate(report)
        self.assertEqual(result["verdict"], "NOT_VERIFIED")
        self.assertFalse(result["all_pass"])
        missing = [g for g in result["gates"] if g["actual"] is None]
        self.assertEqual(len(missing), 1)
        self.assertIn("指标缺失", missing[0]["note"])


if __name__ == "__main__":
    unittest.main()
