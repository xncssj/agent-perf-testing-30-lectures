"""分支覆盖测试：由基准报告推导路径/节点/分支覆盖率（不重复全量跑测）。

课程第 23 讲的覆盖三角：路径覆盖 = 实际路径集合 ⊇ 契约路径集合；
节点覆盖 = 图可达节点全部被触发；分支覆盖 = 实际分支 ⊇ 契约分支。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from tests.performance.common import load_tickets, stamp_and_write  # noqa: E402


def find_latest(reports_dir: Path, key: str) -> Path | None:
    files = sorted(glob.glob(str(reports_dir / f"{key}_*.json")))
    return Path(files[-1]) if files else None


def _load_report_from_judgements(path: Path) -> dict:
    """从 k6 判定明细构造 businesses；缺 path/branch 时给出明确错误（B8 未生效）。"""
    businesses = []
    missing = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "ticket_judged":
                continue
            path_val = row.get("path")
            branch_val = row.get("branch")
            if not path_val or not branch_val:
                missing += 1
                continue
            businesses.append({
                "path": list(path_val),
                "branch": branch_val,
                "intent": row.get("intent") or "",
            })
    if not businesses:
        raise SystemExit(
            f"错误：{path} 中无含 path/branch 的判定行（missing={missing}）；"
            "需 k6-suite B8 改造生效（judgements 增 path/branch 字段）"
        )
    return {"businesses": businesses, "reports_from": path.name}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="分支覆盖测试（优先读 k6 判定明细的 path/branch，回退 benchmark 报告）"
    )
    parser.add_argument("--judgements", help="k6 judgements.ndjson 路径（B8 后含 path/branch）")
    parser.add_argument("--benchmark", help="benchmark_workflow_*.json 路径（缺省取最新）")
    parser.add_argument("--data", help="工单文件路径（缺省 data_dir/tickets.json）")
    args = parser.parse_args(argv)

    settings = Settings()
    started_at = datetime.now().isoformat(timespec="seconds")
    tickets = load_tickets(Path(args.data) if args.data else settings.data_dir / "tickets.json")

    if args.judgements:
        report = _load_report_from_judgements(Path(args.judgements))
    else:
        src_path = Path(args.benchmark) if args.benchmark else find_latest(settings.reports_dir, "benchmark_workflow")
        if src_path is None:
            print("未找到 benchmark 报告，请先运行 tests/performance/benchmark.py 或传 --judgements")
            return 1
        report = json.loads(src_path.read_text(encoding="utf-8"))
        report.setdefault("reports_from", src_path.name)
    source_label = report.get("reports_from") or ""

    # 契约路径/分支
    expected_paths = {}
    expected_branches = {}
    expected_intents = {}
    for t in tickets:
        expected_paths.setdefault(tuple(t.expected_path), 0)
        expected_paths[tuple(t.expected_path)] += 1
        expected_branches.setdefault(t.expected_branch, 0)
        expected_branches[t.expected_branch] += 1
        expected_intents.setdefault(t.expected_intent, 0)
        expected_intents[t.expected_intent] += 1

    actual_paths = {}
    actual_branches = set()
    for b in report["businesses"]:
        p = tuple(b["path"])
        actual_paths.setdefault(p, 0)
        actual_paths[p] += 1
        actual_branches.add(b["branch"])

    # 覆盖率
    path_covered = sum(n for p, n in expected_paths.items() if p in actual_paths)
    branch_covered = sum(n for b, n in expected_branches.items() if b in actual_branches)
    branch_coverage = round(branch_covered / len(tickets), 4)

    all_nodes = {"intent_recognition", "data_retrieval", "knowledge_retrieval",
                 "response_generation", "confidence_check", "clarification"}
    covered_nodes = set()
    for p in actual_paths:
        covered_nodes.update(p)

    path_list = [
        {"path": list(p), "samples": n,
         "covered": p in actual_paths,
         "actual_samples": actual_paths.get(p, 0)}
        for p, n in sorted(expected_paths.items())
    ]
    path_all_consistent = all(p["covered"] for p in path_list)
    branch_all_consistent = all(
        (list(b) == ["intent_recognition", "clarification"]) or b in actual_branches
        for b in expected_branches
    )

    payload = {
        "test_type": "branch_coverage",
        "reports_from": source_label,
        "fingerprint": settings.to_fingerprint_fields(),
        "target_coverage": 0.90,
        "coverage": {
            "branch_coverage": branch_coverage,
            "branch_covered_count": branch_covered,
            "branch_total_count": len(tickets),
            "node_map": f"{len(covered_nodes & all_nodes)}/{len(all_nodes)}",
            "paths": path_list,
            "path_all_consistent": path_all_consistent,
            "branch_all_consistent": branch_all_consistent,
            "branch_set": sorted(actual_branches),
        },
        "intent_distribution": {
            "expected": expected_intents,
            "actual": sorted({b.get("intent") for b in report["businesses"]}),
        },
    }

    out = stamp_and_write(
        settings, "branch_coverage", payload,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        data_path=settings.data_dir / "tickets.json",
    )
    print(f"分支覆盖率 = {branch_coverage}（目标 {payload['target_coverage']}）")
    print(f"节点覆盖 = {payload['coverage']['node_map']}")
    print(f"契约路径全部覆盖 = {path_all_consistent}，节点路径=契约一致性 = {branch_all_consistent}")
    print(f"结果已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())