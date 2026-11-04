"""冻结清单生成 CLI（B9 配套，方案 B6）。

背景：联合门禁 gate_suite.py 要求 6 类专项报告由冻结清单
`evaluation_suite_<id>.json` 组织；清单的建立函数 create_suite_manifest /
write_manifest 已存在于 src/gates/suite.py，但此前只在单元测试中调用、
没有可执行入口。本脚本补齐 CLI，把同轮 6 类报告封成冻结清单。

用法：
    python3 build_evaluation_suite.py --reports-dir reports \\
        --suite-id v13-real23-eval-1 \\
        --dataset-sha256 <sha256> \\
        --report benchmark=benchmark_workflow_20261110_010101.json \\
        --report branch_coverage=branch_coverage_workflow_....json \\
        --report rag_quality=rag_quality_workflow_....json \\
        --report longtail=longtail_workflow_....json \\
        --report critical_path=critical_path_workflow_....json \\
        --report stability=stability_workflow_....json

也可用 --dataset 指定数据文件路径，由工具自动算 sha256。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.gates.suite import (  # noqa: E402
    REQUIRED_REPORT_TYPES,
    SuiteError,
    compute_dataset_sha256,
    create_suite_manifest,
    write_manifest,
)


def _parse_report_args(items: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--report 须为 <type>=<file> 形式，实际 {item!r}")
        rtype, filename = item.split("=", 1)
        rtype = rtype.strip()
        filename = filename.strip()
        if not rtype or not filename:
            raise SystemExit(f"--report 参数不完整：{item!r}")
        mapping[rtype] = filename
    return mapping


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成联合门禁冻结清单 evaluation_suite_<id>.json")
    parser.add_argument("--reports-dir", required=True, help="6 类报告所在目录")
    parser.add_argument("--suite-id", required=True, help="评测套件 id（字母数字与 . _ -）")
    parser.add_argument("--dataset-sha256", help="数据档 sha256（与 --dataset 二选一）")
    parser.add_argument("--dataset", help="数据档文件路径，自动计算 sha256")
    parser.add_argument("--report", action="append", default=[],
                        metavar="TYPE=FILE", help="报告类型=文件名，六类各一条，可重复")
    parser.add_argument("--planned-window-start", help="计划计量窗起点（可选）")
    parser.add_argument("--planned-window-end", help="计划计量窗终点（可选）")
    parser.add_argument("--print-only", action="store_true", help="只打印清单不落盘")
    args = parser.parse_args(argv)

    if args.dataset_sha256:
        dataset_sha256 = args.dataset_sha256
    elif args.dataset:
        dataset_path = Path(args.dataset)
        if not dataset_path.is_file():
            print(f"错误：数据档不存在 {dataset_path}", file=sys.stderr)
            return 2
        dataset_sha256 = compute_dataset_sha256(dataset_path)
    else:
        print("错误：须提供 --dataset-sha256 或 --dataset", file=sys.stderr)
        return 2

    report_paths = _parse_report_args(args.report)
    missing = sorted(set(REQUIRED_REPORT_TYPES) - set(report_paths))
    extra = sorted(set(report_paths) - set(REQUIRED_REPORT_TYPES))
    if missing or extra:
        print(f"错误：报告类型不完整，缺 {missing}，多 {extra}", file=sys.stderr)
        return 2

    planned_window = None
    if args.planned_window_start or args.planned_window_end:
        planned_window = {
            "start": args.planned_window_start,
            "end": args.planned_window_end,
        }

    try:
        manifest = create_suite_manifest(
            reports_dir=args.reports_dir,
            evaluation_suite_id=args.suite_id,
            report_paths=report_paths,
            dataset_sha256=dataset_sha256,
            planned_window=planned_window,
        )
    except SuiteError as exc:
        print(f"错误：冻结清单不成立：{exc}", file=sys.stderr)
        return 1

    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.print_only:
        print(text)
        return 0
    out = write_manifest(manifest, args.reports_dir)
    print(f"已写出冻结清单：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
