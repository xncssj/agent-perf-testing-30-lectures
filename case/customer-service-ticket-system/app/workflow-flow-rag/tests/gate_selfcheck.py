"""门禁判定逻辑自测：验证 门禁判定说明 修正后的分派判定。

无 pytest 依赖，纯 assert，可复跑。判定器实现与文档 3.1 节保持同一语义：
按 gate_type 提取指标值并选择比较方向（gte/lte），禁止统一套用"通过率 >= 阈值"。

注入用例覆盖审查报告复现的两类误判：
- 旧逻辑：安全违规全部超标仍放行（比例口径套计数指标）→ 新逻辑必须阻断；
- 旧逻辑：性能指标全部达标反而被阻断（阈值 5000ms 被当作 0-1 比率）→ 新逻辑必须通过。

运行：python tests/gate_selfcheck.py
"""
from __future__ import annotations

import sys

# 门禁类型 -> (指标提取函数, 比较方向, 单位说明)；与 门禁判定说明 GATE_RULES 一致
GATE_RULES: dict[str, tuple] = {
    "accuracy":    (lambda r: r["pass_rate"], "gte", "比率(0-1)"),
    "coverage":    (lambda r: r["coverage_rate"], "gte", "比率(0-1)"),
    "performance": (lambda r: r["metrics_ms"]["p90"], "lte", "ms"),
    "security":    (lambda r: r["violations"], "lte", "违规数(个)"),
    "cost":        (lambda r: r["cost_per_qualified"], "lte", "元/合格业务"),
    "stability":   (lambda r: r["availability"], "gte", "比率(0-1)"),
}


def judge(gate: dict, report: dict) -> dict:
    """按类型分派判定：提取指标值 -> 按 gte/lte 比较。"""
    gate_type = gate["type"]
    if gate_type not in GATE_RULES:
        raise ValueError(f"Unknown gate type: {gate_type}")
    extractor, direction, unit = GATE_RULES[gate_type]
    metric_value = extractor(report)
    threshold = gate["threshold"]
    passed = metric_value >= threshold if direction == "gte" else metric_value <= threshold
    # 稳定性门禁为联合判定：可用率达标 且 错误率不超预算
    if gate_type == "stability":
        passed = passed and report["error_rate"] <= gate["error_rate_max"]
    return {
        "gate_type": gate_type,
        "metric_value": metric_value,
        "unit": unit,
        "direction": direction,
        "threshold": threshold,
        "passed": passed,
    }


def main() -> int:
    # 用例 1（审查复现）：安全检查全部失败（违规 3 条 > 阈值 0）→ 必须阻断
    r = judge({"type": "security", "threshold": 0}, {"violations": 3})
    assert r["passed"] is False, "安全违规数超限必须阻断"

    # 用例 2（审查复现）：性能 P90 全部达标（3000ms ≤ 5000ms）→ 必须通过
    r = judge({"type": "performance", "threshold": 5000}, {"metrics_ms": {"p90": 3000}})
    assert r["passed"] is True, "性能指标达标（lte 方向）不得误阻断"

    # 用例 3：性能 P90 超标（6000ms > 5000ms）→ 阻断
    r = judge({"type": "performance", "threshold": 5000}, {"metrics_ms": {"p90": 6000}})
    assert r["passed"] is False

    # 用例 4：成本超限（0.8 元/合格业务 > 0.5）→ 阻断
    r = judge({"type": "cost", "threshold": 0.5}, {"cost_per_qualified": 0.8})
    assert r["passed"] is False, "成本门禁（lte 方向）超限必须阻断"

    # 用例 5：准确率低于阈值（0.93 < 0.95，gte 方向）→ 阻断
    r = judge({"type": "accuracy", "threshold": 0.95}, {"pass_rate": 0.93})
    assert r["passed"] is False

    # 用例 6：覆盖率达标（0.92 >= 0.90）→ 通过
    r = judge({"type": "coverage", "threshold": 0.90}, {"coverage_rate": 0.92})
    assert r["passed"] is True

    # 用例 7：稳定性联合判定——可用率与错误率双达标 → 通过
    r = judge(
        {"type": "stability", "threshold": 0.995, "error_rate_max": 0.005},
        {"availability": 0.996, "error_rate": 0.004},
    )
    assert r["passed"] is True

    # 用例 8：稳定性联合判定——可用率达标但错误率超预算 → 阻断
    r = judge(
        {"type": "stability", "threshold": 0.995, "error_rate_max": 0.005},
        {"availability": 0.997, "error_rate": 0.02},
    )
    assert r["passed"] is False, "稳定性门禁必须双条件联合判定"

    # 用例 9：未登记的门禁类型 → 显式报错（禁止静默落入默认比较）
    try:
        judge({"type": "unknown", "threshold": 1}, {})
        raise AssertionError("未登记的门禁类型必须报错")
    except ValueError:
        pass

    print("gate_selfcheck 全部通过：类型分派判定 / gte-lte 方向 / 稳定性联合判定 / 未知类型报错")
    return 0


if __name__ == "__main__":
    sys.exit(main())
