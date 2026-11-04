"""测试报告生成：把 reports/*.json 实验结果汇总为 Markdown 报告。

用法：python scripts/build_report.py
产物：reports/summary_<日期>.md，仅汇总真实运行产生的结果文件。
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = PROJECT_ROOT / "reports"


def _fmt_ms(v) -> str:
    return f"{v} ms" if v else "-"


def _batch_label(payload: dict) -> str:
    """v2 报告使用 batch_id；v1 历史报告保留原 run_id 显示。"""
    return payload.get("batch_id") or payload.get("run_id") or "-"


def render_benchmark(payload: dict) -> str:
    agg = payload.get("aggregate", {})
    e2e = agg.get("时延指标(mm)", {}).get("E2E", {})
    ttft = agg.get("时延指标(mm)", {}).get("TTFT", {})
    rate = agg.get("合格率", {})
    lines = [
        "### 基准测试",
        "",
        f"- 批次 ID：`{_batch_label(payload)}`",
        f"- 采样业务数：{payload.get('samples')}",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| E2E P90 | {_fmt_ms(e2e.get('p90'))} |",
        f"| E2E P99 | {_fmt_ms(e2e.get('p99'))} |",
        f"| E2E Mean | {_fmt_ms(e2e.get('mean'))} |",
        f"| TTFT P90 | {_fmt_ms(ttft.get('p90'))} |",
        f"| TTFT P99 | {_fmt_ms(ttft.get('p99'))} |",
        f"| 合格业务百分比 | {rate.get('合格业务百分比')}% |",
        f"| 业务 Goodput | {agg.get('业务Goodput(合格业务/秒)')} |",
        "",
    ]
    return "\n".join(lines)


def render_capacity(payload: dict) -> str:
    lines = ["### 容量测试（阶梯加压）", "", "| 并发 | E2E P90 (ms) | E2E P99 (ms) | 合格率 (%) | Goodput |", "|------|-------------|-------------|-----------|---------|"]
    for lv in payload.get("levels", []):
        e2e = lv.get("时延指标(mm)", {}).get("E2E", {})
        lines.append(
            f"| {lv['concurrency']} | {e2e.get('p90', '-')} | {e2e.get('p99', '-')} "
            f"| {lv.get('合格率', {}).get('合格业务百分比', '-')} "
            f"| {lv.get('业务Goodput(合格业务/秒)', '-')} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_stability(payload: dict) -> str:
    agg = payload.get("aggregate", {})
    e2e = agg.get("时延指标(mm)", {}).get("E2E", {})
    ttft = agg.get("时延指标(mm)", {}).get("TTFT", {})
    rate = agg.get("合格率", {})
    return "\n".join(
        [
            "### 稳定性测试",
            "",
            f"- 批次 ID：`{_batch_label(payload)}`",
            f"- 并发：{payload.get('args', {}).get('concurrency')}，时长：{payload.get('args', {}).get('duration')}s",
            f"- 业务数：{payload.get('business_count')}",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| E2E P90 | {_fmt_ms(e2e.get('p90'))} |",
            f"| E2E P99 | {_fmt_ms(e2e.get('p99'))} |",
            f"| TTFT P90 | {_fmt_ms(ttft.get('p90'))} |",
            f"| 合格业务百分比 | {rate.get('合格业务百分比')}% |",
            f"| 业务 Goodput | {agg.get('业务Goodput(合格业务/秒)')} |",
            "",
        ]
    )


def main() -> None:
    json_files = sorted(glob.glob(str(REPORTS_DIR / "*.json")))
    if not json_files:
        print("未找到实验结果（reports/*.json）。请先运行基准/容量/稳定性测试。")
        sys.exit(0)

    blocks = [
        "# 客服工单 Single-Agent 性能测试报告（自动生成）",
        "",
        f"> 实验数据来源：`reports/*.json`（真实运行产物），生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    for fp in json_files:
        with Path(fp).open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        t = payload.get("test_type")
        renderer = {
            "benchmark": render_benchmark,
            "capacity": render_capacity,
            "stability": render_stability,
        }.get(t)
        if renderer:
            blocks.append(renderer(payload))

    summary = Path(REPORTS_DIR) / f"summary_{datetime.now().strftime('%Y%m%d')}.md"
    summary.write_text("\n".join(blocks), encoding="utf-8")
    print(f"报告已生成：{summary}")


if __name__ == "__main__":
    main()
    sys.exit(0)