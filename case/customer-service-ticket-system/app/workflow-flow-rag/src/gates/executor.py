"""联合门禁执行器：六类门禁一次性评估（门禁设计说明）。

门禁口径与 tests/gate_selfcheck.py 的 GATE_RULES 一致：
- 性能  e2e_p90_ms        gte 上限（≤ 阈值）
- 覆盖  branch_coverage   gte 下限（≥ 阈值）
- 安全  security_violations lte 上限
- 成本  cost_per_business lte 上限
- 稳定  availability      gte 下限 / error_rate lte 上限
- 质量  qualified_rate    gte 下限
三态结论（006 契约 §3）：任一指标缺失 → NOT_VERIFIED（不填理想值，
与 002 压测门禁同语义）；证据完整时按阈值判 PASS / FAIL。
"""
from __future__ import annotations

from ..utils.logger import get_logger

logger = get_logger("gates.executor")

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_NOT_VERIFIED = "NOT_VERIFIED"

# 门禁规范：名称 -> (指标, 方向, 默认阈值)
# YAML 键名可能不同（latency/branch_coverage/cost_per_qualified/stability），
# 以 CANONICAL 归一化后再评估；缺失门禁以默认值兜底。
CANONICAL = {
    "accuracy": {"metric": "qualified_rate", "direction": "gte", "threshold": 0.90},
    "performance": {"metric": "e2e_p90_ms", "direction": "lte", "threshold": 5000.0},
    "coverage": {"metric": "branch_coverage", "direction": "gte", "threshold": 0.90},
    "security": {"metric": "security_violations", "direction": "lte", "threshold": 0},
    "cost": {"metric": "cost_per_business", "direction": "lte", "threshold": 0.5},
    "availability": {"metric": "availability", "direction": "gte", "threshold": 0.995},
    "error_rate": {"metric": "error_rate", "direction": "lte", "threshold": 0.005},
}

# YAML 键名（或类型）到规范名的归一化
_ALIAS_TO_CANONICAL = {
    "accuracy": "accuracy",
    "quality": "accuracy",
    "performance": "performance",
    "latency": "performance",
    "coverage": "coverage",
    "branch_coverage": "coverage",
    "security": "security",
    "cost": "cost",
    "cost_per_qualified": "cost",
    "availability": "availability",
    "stability": "availability",
    "error_rate": "error_rate",
}


class GateExecutor:
    """按报告指标评估门禁，返回通过清单与联合结论。"""

    def __init__(self, config_gates: dict | None = None) -> None:
        self.gates: dict[str, dict] = {}
        for name, spec in CANONICAL.items():
            self.gates[name] = {"metric": spec["metric"],
                                "direction": spec["direction"],
                                "threshold": spec["threshold"]}
        for key, rule in (config_gates or {}).items():
            canonical = _ALIAS_TO_CANONICAL.get(str(key).lower()) or \
                _ALIAS_TO_CANONICAL.get((rule or {}).get("type", ""))
            if canonical is None or canonical not in self.gates:
                continue
            thr = rule.get("threshold")
            if thr is not None:
                self.gates[canonical]["threshold"] = float(thr)

    def evaluate(self, report: dict) -> dict:
        provenance = report.get("_provenance") if isinstance(report, dict) else None
        results = []
        all_pass = True
        verified = True
        for name, spec in self.gates.items():
            metric = spec["metric"]
            actual = report.get(metric)
            direction = spec["direction"]
            threshold = spec["threshold"]
            if actual is None:
                # 缺字段不是 FAIL：门禁不能在证据不足时给出通过性结论
                # （006 G02：缺失字段不得填 0/1 或配置默认值）。
                verified = False
                passed, note = False, "指标缺失：证据不足，不填理想值"
            elif direction == "gte":
                passed = bool(actual >= threshold)
                note = f"{actual:.3f} >= {threshold}"
            else:
                passed = bool(actual <= threshold)
                note = f"{actual:.3f} <= {threshold}"
            all_pass = all_pass and passed
            results.append({
                "gate": name,
                "metric": metric,
                "direction": direction,
                "threshold": threshold,
                "actual": actual,
                "passed": passed,
                "note": note,
                "source_report": (provenance or {}).get(metric),
            })
        if not verified:
            verdict = VERDICT_NOT_VERIFIED
            conclusion = "证据不足，联合门禁不能判定（NOT_VERIFIED）"
        elif all_pass:
            verdict = VERDICT_PASS
            conclusion = "联合门禁通过"
        else:
            verdict = VERDICT_FAIL
            conclusion = "联合门禁不通过"
        return {
            "gates": results,
            "all_pass": all_pass and verified,
            "verdict": verdict,
            "conclusion": conclusion,
        }