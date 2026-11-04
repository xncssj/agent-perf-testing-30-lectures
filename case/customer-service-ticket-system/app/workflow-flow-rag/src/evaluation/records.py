"""逐笔评测记录（006 R04 / 契约 §5）：与执行身份相互解析的 EvaluationRecord。

- 以 ``task_id``/``run_id``/``trace_id`` 关联执行；本案例 run_id 即 Langfuse
  Trace ID（契约 §2），可反向定位同一结果的轨迹；
- 记录工单集版本/哈希与判定规则版本；各项缺证据时保留空值与原因，
  不推断为通过——事实正确性不能仅由关键词命中推断，逐笔护栏明细未
  持久化前安全判定为空；
- 质量汇总只能从逐笔判定产生，分别注明分子（符合条件样本数）、分母
  （适用且有效样本数）、数据集与规则版本；缺适用样本不显示为 0%/100%；
- 工具执行成功率与 token 比例只作诊断信号，不替代正确性判定。

数据来源：benchmark 报告逐笔 ``businesses``（含 run_id/stop_reason/要素命中
明细）；确定性规则仍是主判定，人工复核用于规则误判与样本入库（R05）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

EVAL_RULES_VERSION = "eval-rules-v1"


def _basis(value, basis: str) -> dict:
    return {"value": value, "basis": basis}


def build_evaluation_records(benchmark_report: dict, dataset_sha256: str) -> dict:
    """由 benchmark 报告构建逐笔 EvaluationRecord 集合。"""
    run_id = benchmark_report.get("run_id") or ""
    fingerprint = benchmark_report.get("fingerprint") or {}
    slo_e2e_ms = fingerprint.get("slo_e2e_ms")
    businesses = benchmark_report.get("businesses") or []

    records: list[dict] = []
    for b in businesses:
        quality = b.get("quality") or {}
        b_run_id = b.get("run_id") or run_id
        facts_expected = quality.get("facts_expected") or 0
        facts_hit = quality.get("facts_hit") or 0
        keywords_expected = quality.get("keywords_expected") or 0
        keywords_missed = quality.get("keywords_missed") or []

        # 事实正确性：要素命中只是确定性规则的子串判定，不能单独作为
        # "事实正确"的结论；无要素期望或需要语义复核时保留空值与原因。
        if facts_expected > 0 and not keywords_missed:
            fact_correct = _basis(
                True, f"benchmark 要素命中 {facts_hit}/{facts_expected}"
                       "（确定性子串判定，语义复核待人工）")
        elif facts_expected > 0:
            fact_correct = _basis(
                False, f"benchmark 要素缺失 {facts_expected - facts_hit}"
                       f"/{facts_expected}")
        else:
            fact_correct = _basis(None, "该工单无事实判定依据（expected_facts 为空）")

        # 安全判定：基准报告只有聚合层安全指标，未逐笔持久化护栏明细
        safety = _basis(None, "benchmark 报告未逐笔持久化护栏明细；"
                              "聚合口径见报告 aggregate.安全指标")

        # SLO：完整答复 E2E 逐笔实测 vs 指纹阈值（落盘形态为顶层 e2e_ms，
        # 兼容 classify 内存形态 metrics.e2e_ms）
        e2e = b.get("e2e_ms")
        if e2e is None:
            e2e = (b.get("metrics") or {}).get("e2e_ms")
        if e2e is not None and slo_e2e_ms is not None:
            slo = _basis(e2e <= slo_e2e_ms, f"e2e_ms={e2e} ≤ {slo_e2e_ms}")
        else:
            slo = _basis(None, "缺少逐笔 e2e_ms 或指纹 SLO 阈值")

        # 工具核查：工具名清单（006 R04 增强）；选择/参数/顺序/权限核查
        # 需逐笔轨迹与受控产物复核
        tool_calls = b.get("tools")
        if tool_calls is None:
            tool_calls = (b.get("metrics") or {}).get("tool_calls") or []
        tool_checks = _basis(
            [{"tool": t} for t in tool_calls] or None,
            "仅工具名清单；选择/参数/顺序/权限核查需逐笔轨迹与受控产物复核",
        )

        records.append({
            "task_id": b.get("ticket_id"),
            "run_id": b_run_id,
            "trace_id": b_run_id,
            "batch_id": b.get("batch_id") or benchmark_report.get("batch_id"),
            "rule_version": EVAL_RULES_VERSION,
            "dataset_sha256": dataset_sha256,
            "status": b.get("status"),
            "stop_reason": b.get("stop_reason"),
            "task_completion": _basis(
                bool(b.get("qualified")),
                "benchmark 四级判定（AND 语义）levels.qualified"),
            "fact_correct": fact_correct,
            "safety": safety,
            "slo_met": slo,
            "tool_checks": tool_checks,
            "qualified": bool(b.get("qualified")),
        })

    return {
        "schema": "evaluation-records-v1",
        "rule_version": EVAL_RULES_VERSION,
        "dataset_sha256": dataset_sha256,
        "source_report": benchmark_report.get("run_id"),
        "fingerprint": fingerprint,
        "records": records,
        "summary": _summarize(records),
    }


def _summarize(records: list[dict]) -> dict:
    """质量汇总：分子/分母/规则版本；缺适用样本记 unknown，不显示 0%/100%。"""
    def rate(field: str) -> dict:
        applicable = [r for r in records if r[field]["value"] is not None]
        passed = [r for r in applicable if r[field]["value"]]
        if not applicable:
            return {"numerator": None, "denominator": 0,
                    "rate": None, "note": "无适用样本"}
        return {
            "numerator": len(passed),
            "denominator": len(applicable),
            "rate": round(len(passed) / len(applicable), 4),
        }

    return {
        "rule_version": EVAL_RULES_VERSION,
        "task_completion": rate("task_completion"),
        "fact_correct": rate("fact_correct"),
        "slo_met": rate("slo_met"),
        "total_records": len(records),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("用法：python3 -m src.evaluation.records <benchmark.json> <out.json> "
              "<dataset_sha256>")
        return 2
    report = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    payload = build_evaluation_records(report, argv[2])
    Path(argv[1]).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"records": len(payload["records"]),
                      "summary": payload["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
