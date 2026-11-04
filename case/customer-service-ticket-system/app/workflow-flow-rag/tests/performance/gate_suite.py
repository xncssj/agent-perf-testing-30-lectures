"""联合门禁评估（课程第 24 讲）：聚合各专项结果，七项门禁一次评估。

006 R01 整改后的取数方式（G01/G02）：
- 六类专项报告必须由同一 ``evaluation_suite_id`` 的冻结清单组织；
  门禁只读取清单内文件，不再按文件名排序取"最新"（混轮根因）；
- 缺报告、指纹/数据集不一致、窗口不可核验、缺字段 → ``NOT_VERIFIED``
  并给出原因码，不填理想值；
- 证据完整时按 ``workflow_config.yaml`` 既有七项阈值判 PASS / FAIL。

用法：
    python tests/performance/gate_suite.py --suite reports/evaluation_suite_<id>.json

退出码：PASS=0，FAIL=1，NOT_VERIFIED=2（含未指定清单）。
补充安全门禁双态演示：正常答复违规数=0 通过；注入含手机号的答复则拦截失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.gates import suite as suite_mod  # noqa: E402
from src.gates.executor import GateExecutor  # noqa: E402
from src.gates.report_metrics import MissingReportError, build_gate_metrics  # noqa: E402
from src.workflow.guardrail import guardrail  # noqa: E402

# 各门禁指标的来源报告类型（证据溯源，随逐项结果写入 source_report）
METRIC_SOURCE = {
    "qualified_rate": "benchmark",
    "e2e_p90_ms": "benchmark",
    "security_violations": "benchmark",
    "cost_per_business": "benchmark",
    "branch_coverage": "branch_coverage",
    "availability": "stability",
    "error_rate": "stability",
}


def _write_report(settings: Settings, payload: dict) -> Path:
    out = settings.reports_dir / f"gate_workflow_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _not_verified(settings: Settings, reason_codes: list[dict], suite_id: str | None) -> int:
    """证据不足时落盘 NOT_VERIFIED 报告；缺证据不产生通过性结论。"""
    # 证据路径写相对路径，避免把执行机器绝对路径带进产物
    for r in reason_codes:
        p = r.get("evidence_path", "")
        if p.startswith(str(Path.cwd())):
            r["evidence_path"] = str(Path(p).relative_to(Path.cwd()))
    payload = {
        "test_type": "gate",
        "fingerprint": settings.to_fingerprint_fields(),
        "evaluation_suite_id": suite_id,
        "verdict": suite_mod.VERDICT_NOT_VERIFIED,
        "conclusion": "证据不足，联合门禁不能判定（NOT_VERIFIED）",
        "all_pass": False,
        "reason_codes": reason_codes,
    }
    out = _write_report(settings, payload)
    print(json.dumps({"verdict": payload["verdict"],
                      "reason_codes": [r["code"] for r in reason_codes]},
                     ensure_ascii=False))
    print(f"结果已写入：{out}")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段三七项联合门禁（同轮评测套件）")
    parser.add_argument(
        "--suite", required=False, default=None,
        help="冻结的评测套件清单 JSON（evaluation_suite_<id>.json）；"
             "文件名时间排序不能代替批次清单",
    )
    args = parser.parse_args()

    settings = Settings()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    if not args.suite:
        return _not_verified(settings, [{
            "code": "SUITE_NOT_SPECIFIED",
            "detail": "未指定 --suite 冻结清单；按文件名取最新会混入不同评测轮次"
                      "（G01），缺清单不产生门禁结论",
            "evidence_path": str(settings.reports_dir),
        }], suite_id=None)

    suite_path = Path(args.suite)
    if not suite_path.exists():
        return _not_verified(settings, [{
            "code": "SUITE_MANIFEST_MISSING",
            "detail": f"清单文件不存在：{suite_path}",
            "evidence_path": str(suite_path),
        }], suite_id=None)

    try:
        manifest = suite_mod.load_manifest(suite_path)
    except suite_mod.SuiteError as exc:
        return _not_verified(settings, [{
            "code": "SUITE_MANIFEST_INVALID", "detail": str(exc),
            "evidence_path": str(suite_path),
        }], suite_id=None)

    reports, issues = suite_mod.validate_suite(manifest, settings.reports_dir)
    if issues:
        return _not_verified(settings, issues, suite_id=manifest["evaluation_suite_id"])

    # 门禁指标统一由 report_metrics 构建：安全违规数取基准报告的逐笔护栏聚合，
    # 缺字段抛 MissingReportError → NOT_VERIFIED，不用理想值兜底（待办 2b / 006 G02）。
    try:
        report = build_gate_metrics(
            reports.get("benchmark"),
            reports.get("branch_coverage"),
            reports.get("stability"),
        )
    except MissingReportError as exc:
        return _not_verified(settings, [{
            "code": "FIELD_MISSING", "detail": str(exc),
            "evidence_path": str(suite_path),
        }], suite_id=manifest["evaluation_suite_id"])

    report["_provenance"] = METRIC_SOURCE

    # 安全门禁双态演示：注入含手机号的答复应被拦截（与实测违规数分开呈现）
    injected = guardrail.check("可直接拨打 13812345678 联系客服。")
    result = GateExecutor(settings.gate_rules).evaluate(report)
    report.pop("_provenance", None)
    report.update(result)

    frozen_list = {
        rtype: {
            "file": manifest["reports"][rtype]["file"],
            "report_run_id": manifest["reports"][rtype]["report_run_id"],
            "window": [manifest["reports"][rtype]["started_at"],
                       manifest["reports"][rtype]["ended_at"]],
        }
        for rtype in suite_mod.REQUIRED_REPORT_TYPES
    }
    payload = {
        "test_type": "gate",
        "fingerprint": settings.to_fingerprint_fields(),
        "evaluation_suite_id": manifest["evaluation_suite_id"],
        "dataset_sha256": manifest["dataset_sha256"],
        "frozen_reports": frozen_list,
        "report": report,
        "gates": result["gates"],
        "all_pass": result["all_pass"],
        "verdict": result["verdict"],
        "conclusion": result["conclusion"],
        "security_blocked_demo": {
            "answer_has_phone": injected["blocked"],
            "violations": injected["violations"],
        },
        "evidence": {
            "benchmark": reports["benchmark"].get("run_id"),
            "branch": reports["branch_coverage"]["coverage"]["branch_coverage"],
            "rag_precision": reports["rag_quality"]["quality"]["precision_top1"],
            "longtail": f"{reports['longtail']['passed']}/{reports['longtail']['total']}",
            "critical": reports["critical_path"]["checkpoint_enabled"],
            "stability_availability": reports["stability"].get("availability"),
        },
    }
    out = _write_report(settings, payload)
    print(json.dumps({"verdict": result["verdict"], "all_pass": result["all_pass"],
                      "evaluation_suite_id": manifest["evaluation_suite_id"]},
                     ensure_ascii=False))
    for g in result["gates"]:
        print(f"  门禁[{g['gate']}] {g['direction']} {g['actual']} "
              f"{'✔' if g['passed'] else '✘'}（来源：{g.get('source_report')}）")
    print(f"结果已写入：{out}")
    if result["verdict"] == suite_mod.VERDICT_PASS:
        return 0
    if result["verdict"] == suite_mod.VERDICT_FAIL:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
