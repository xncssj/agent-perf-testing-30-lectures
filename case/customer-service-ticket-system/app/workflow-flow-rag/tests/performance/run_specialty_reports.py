"""六类专项报告编排（方案 B7）。

背景：联合门禁 gate_suite.py 需要 6 类专项报告 + 冻结清单；此前六类报告
没有编排入口，需人工逐个跑。本脚本按固定顺序执行六类报告脚本，定位最新
产物，生成 evaluation_suite_<id>.json 冻结清单，最后调用 gate_suite.py 出
七项判定表。

用法（阶段三目录下）：
    python3 run_specialty_reports.py --suite-id v13-real23-eval-1 \\
        --dataset testing/test-data/performance-v1.1/out/reference-balanced-uniform/load-stage3.json \\
        [--skip-gate] [--dry-run]

注意：六类报告脚本本身需要服务/数据就绪；本脚本只编排与校验，不掩盖
脚本失败（任一失败即停止并返回非零）。
"""
from __future__ import annotations

import argparse
import glob
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "reports"

# 顺序即依赖：benchmark 先跑，branch_coverage 读它
SCRIPTS = [
    ("benchmark", "tests.performance.benchmark", "benchmark_workflow"),
    ("branch_coverage", "tests.performance.branch_coverage", "branch_coverage_workflow"),
    ("rag_quality", "tests.performance.rag_quality", "rag_quality_workflow"),
    ("longtail", "tests.performance.longtail", "longtail_workflow"),
    ("critical_path", "tests.performance.critical_path", "critical_path_workflow"),
    ("stability", "tests.performance.stability", "stability_workflow"),
]


def _latest(prefix: str) -> Path | None:
    files = sorted(glob.glob(str(REPORTS_DIR / f"{prefix}_*.json")),
                   key=lambda p: Path(p).stat().st_mtime)
    return Path(files[-1]) if files else None


def _run_module(module: str, dry_run: bool) -> int:
    cmd = [sys.executable, "-m", module]
    print(f"[run] {' '.join(cmd)}")
    if dry_run:
        return 0
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="六类专项报告编排 + 冻结清单 + 联合门禁（B7）")
    parser.add_argument("--suite-id", required=True)
    parser.add_argument("--dataset", required=True, help="当轮数据档文件（用于 dataset_sha256）")
    parser.add_argument("--skip-gate", action="store_true", help="只出报告与清单，不跑 gate_suite")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令不执行")
    parser.add_argument("--report-report-dir", default=str(REPORTS_DIR))
    args = parser.parse_args(argv)

    for label, module, _prefix in SCRIPTS:
        code = _run_module(module, args.dry_run)
        if code != 0:
            print(f"错误：{label} 报告脚本失败（exit={code}），停止编排", file=sys.stderr)
            return code or 1

    report_args = []
    missing = []
    for label, _module, prefix in SCRIPTS:
        latest = _latest(prefix)
        if latest is None:
            missing.append(f"{label}({prefix})")
            continue
        report_args.extend(["--report", f"{label}={latest.name}"])
    if missing:
        print(f"错误：未找到产物：{missing}", file=sys.stderr)
        return 2

    build_cmd = [sys.executable, "tests/performance/build_evaluation_suite.py",
                 "--reports-dir", args.report_report_dir,
                 "--suite-id", args.suite_id, "--dataset", args.dataset] + report_args
    print(f"[run] {' '.join(build_cmd)}")
    if not args.dry_run:
        code = subprocess.call(build_cmd, cwd=str(PROJECT_ROOT))
        if code != 0:
            return code

    if args.skip_gate:
        print("跳过联合门禁（--skip-gate）")
        return 0
    suite_path = Path(args.report_report_dir) / f"evaluation_suite_{args.suite_id}.json"
    gate_cmd = [sys.executable, "-m", "tests.performance.gate_suite", "--suite", str(suite_path)]
    print(f"[run] {' '.join(gate_cmd)}")
    if args.dry_run:
        return 0
    return subprocess.call(gate_cmd, cwd=str(PROJECT_ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
