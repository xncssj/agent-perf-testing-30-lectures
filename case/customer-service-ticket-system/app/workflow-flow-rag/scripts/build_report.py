"""测试报告生成：把 reports/*.json 实验结果汇总为 Markdown，并附联合门禁结论。

用法：python scripts/build_report.py
产物：reports/summary_<日期>.md。只汇总真实运行产生的结果文件。
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.gates.executor import GateExecutor  # noqa: E402
from src.gates.report_metrics import MissingReportError, build_gate_metrics  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "reports"


def _fmt_ms(v) -> str:
    return f"{v} ms" if v else "-"


def _e2e(agg: dict) -> dict:
    return agg.get("时延指标(mm)", {}).get("E2E", {})


def render_benchmark(payload: dict) -> str:
    agg = payload.get("aggregate", {})
    e2e = _e2e(agg)
    rate = agg.get("合格率", {})
    cost = agg.get("成本指标", {})
    return "\n".join([
        "## 基准测试（主路径 + 分支配比）",
        "",
        f"- Run ID：`{payload.get('run_id')}`",
        f"- 采样业务数：{payload.get('samples')}（主路径 {payload.get('samples_main', '-')}，分支 {payload.get('samples_branch', '-')}）",
        "- 口径：四级业务判定；结果合格 = 关键词 AND 事实 AND 路径=预期 AND 分支=预期",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| E2E P90 | {_fmt_ms(e2e.get('p90'))} |",
        f"| E2E P99 | {_fmt_ms(e2e.get('p99'))} |",
        f"| E2E Mean | {_fmt_ms(e2e.get('mean'))} |",
        f"| 合格业务百分比 | {rate.get('合格业务百分比')}% |",
        f"| 业务 Goodput | {agg.get('业务Goodput(合格业务/秒)')} |",
        f"| 路径一致率 | {payload.get('path_match_rate')}% |",
        f"| 分支一致率 | {payload.get('branch_match_rate')}% |",
        f"| 总 token | {cost.get('总token')} |",
        f"| 每合格业务 token | {cost.get('每合格业务token')} |",
        f"| 成功业务成本 | {cost.get('成功业务成本(元/合格业务)')} 元 |",
        "",
    ])


def render_branch_coverage(payload: dict) -> str:
    cover = payload.get("coverage", {})
    rows = [
        "## 分支覆盖测试",
        "",
        "| 路径 | 覆盖 | 采样 |",
        "|------|------|------|",
    ]
    for p in cover.get("paths", []):
        ok = "是" if p.get("covered") else "否"
        rows.append(f"| `{p.get('path')}` | {ok} | {p.get('samples')} |")
    rows.extend([
        "",
        f"- 目录/节点覆盖：{cover.get('node_map', '')}",
        f"- 分支覆盖率：`{cover.get('branch_coverage')}`（目标 {payload.get('target_coverage')}）",
        f"- 全部分支路径与数据集口径一致：{'是' if cover.get('path_all_consistent') else '否'}",
        "",
    ])
    return "\n".join(rows)


def render_rag_quality(payload: dict) -> str:
    q = payload.get("quality", {})
    return "\n".join([
        "## RAG 检索质量测试",
        "",
        f"- 检索模式：`{q.get('search_mode')}`，top_k={q.get('top_k')}，索引文档数：{q.get('docs_count')}",
        f"- 精确命中率（top1）：{q.get('precision_top1')}",
        f"- 精度@3：{q.get('precision_at_3')}",
        f"- P90 检索耗时：{_fmt_ms(q.get('p90_latency_ms'))}",
        f"- 单文档检索重复性：{q.get('repeatable')}",
        f"- 样例：{json.dumps(q.get('samples', [])[:3], ensure_ascii=False)}",
        "",
    ])


def render_critical_path(payload: dict) -> str:
    lines = [
        "## 关键路径与故障恢复",
        "",
        f"- 关键路径：`{payload.get('critical_path')}`",
        f"- 关键路径节点：{payload.get('key_nodes')}",
        f"- Checkpoint 已启用：{payload.get('checkpoint_enabled')}；已幂等重跑节点：{payload.get('idempotent_nodes')}",
        "",
        "| 场景 | 状态 | 说明 |",
        "|------|------|------|",
    ]
    for s in payload.get("scenarios", []):
        lines.append(f"| {s.get('name')} | {s.get('status')} | {s.get('note')} |")
    lines.append("")
    return "\n".join(lines)


def render_longtail(payload: dict) -> str:
    rows = [
        "## 低频长尾分支（合成场景 + 定向数据增强）",
        "",
        "| 场景 | 分支 | 意图 | 结果 |",
        "|------|------|------|------|",
    ]
    for s in payload.get("scenarios", []):
        rows.append(f"| {s.get('name')} | {s.get('branch')} | {s.get('intent')} | {s.get('result')} |")
    rows.extend([
        "",
        f"- 定位结论：`{payload.get('conclusion')}`",
        f"- 长尾缺口/落地建议：`{payload.get('gap_conclusion')}`",
        "",
    ])
    return "\n".join(rows)


def render_gate(payload: dict) -> str:
    rows = [
        "## 联合门禁评估",
        "",
        "| 门禁 | 指标 | 方向 | 阈值 | 实测 | 通过 |",
        "|------|------|------|------|------|------|",
    ]
    for g in payload.get("gates", []):
        rows.append(
            f"| {g.get('gate')} | {g.get('metric')} | {g.get('direction')} "
            f"| {g.get('threshold')} | {g.get('actual')} | {'通过' if g.get('passed') else '不通过'} |"
        )
    rows.extend([
        "",
        f"- 联合门禁结论：**{payload.get('conclusion')}**",
        f"- 继续发布：{'是' if payload.get('all_pass') else '否（需修复后重新评估）'}",
        "",
    ])
    return "\n".join(rows)


def main() -> None:
    settings = Settings()
    json_files = sorted(glob.glob(str(REPORTS_DIR / "*.json")))
    if not json_files:
        print("未找到实验结果（reports/*.json）。请先运行 tests/performance 套件。")
        sys.exit(0)

    blocks = [
        "# 客服工单 Workflow+RAG 性能测试报告（自动生成）",
        "",
        f"> 实验数据来源：`reports/*.json`（真实运行产物），生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        f"> 环境指纹：`{json.dumps(settings.to_fingerprint_fields(), ensure_ascii=False)}`",
        "",
    ]
    report_map: dict = {}
    for fp in json_files:
        with Path(fp).open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        report_map[payload.get("test_type")] = payload

    renderer = {
        "benchmark": render_benchmark,
        "branch_coverage": render_branch_coverage,
        "rag_quality": render_rag_quality,
        "critical_path": render_critical_path,
        "longtail": render_longtail,
    }
    for t, fn in renderer.items():
        if t in report_map:
            blocks.append(fn(report_map[t]))

    # 联合门禁：只输出一小节（历史上循环里渲染过 gate 报告、末尾又复算一次，
    # 导致同名小节重复且复算取数错误判出一片 None——2026-09-12 待办 2c）。
    # 指标统一走 report_metrics，安全违规数取基准报告的逐笔护栏聚合。
    try:
        metrics = build_gate_metrics(
            report_map.get("benchmark"),
            report_map.get("branch_coverage"),
            report_map.get("stability"),
        )
        gate = GateExecutor(settings.gate_rules).evaluate(metrics)
        blocks.append(render_gate({**gate, "gate": metrics}))
    except MissingReportError as exc:
        blocks.extend([
            "## 联合门禁评估",
            "",
            f"- 未评估：{exc}",
            "",
        ])

    summary = Path(REPORTS_DIR) / f"summary_{datetime.now().strftime('%Y%m%d')}.md"
    summary.write_text("\n".join(blocks), encoding="utf-8")
    print(f"报告已生成：{summary}")


if __name__ == "__main__":
    main()
    sys.exit(0)