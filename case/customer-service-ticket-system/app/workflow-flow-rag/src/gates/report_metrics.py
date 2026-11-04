"""门禁指标来源：从已落盘的专项报告构建门禁判定所需指标（单一事实源）。

`gate_suite.py`（产出 gate 报告）与 `scripts/build_report.py`（生成汇总
Markdown）共用本模块，避免两处各写一遍取数逻辑——此前汇总报告出现两个
同名「联合门禁评估」小节、其中一个因取数错误判出一片 None，根因就是
重复实现（2026-09-12 待办 2c）。

安全违规数取自基准报告的安全聚合（逐笔护栏结果），不再硬编码 0
（2026-09-12 待办 2b）。
"""
from __future__ import annotations


class MissingReportError(ValueError):
    """必要专项报告缺失或字段不全。"""


def _need(payload: dict | None, name: str) -> dict:
    if not payload:
        raise MissingReportError(f"缺少专项报告：{name}")
    return payload


def build_gate_metrics(
    benchmark: dict | None,
    branch_coverage: dict | None,
    stability: dict | None = None,
) -> dict:
    """由专项报告构建门禁指标字典（键与 GateExecutor 约定一致）。

    安全违规数来自 benchmark 的 `安全指标.违规条数`；若该字段缺失，说明
    基准报告出自未接入安全聚合的旧版本，此时明确失败并提示重跑，而不是
    静默按 0 通过（不允许用缺失数据冒充"无违规"）。
    """
    bench = _need(benchmark, "benchmark")
    branch = _need(branch_coverage, "branch_coverage")

    agg = bench.get("aggregate") or {}
    rate = (agg.get("合格率") or {}).get("合格业务百分比")
    e2e = ((agg.get("时延指标(mm)") or {}).get("E2E") or {}).get("p90")
    cost = (agg.get("成本指标") or {}).get("成功业务成本(元/合格业务)")
    if rate is None or e2e is None:
        raise MissingReportError("benchmark 报告缺少合格率或 E2E P90，无法判定门禁")

    security = agg.get("安全指标")
    if security is None:
        raise MissingReportError(
            "benchmark 报告缺少安全聚合（安全指标）；请用接入逐笔护栏采集后的"
            "版本重跑 benchmark，不得以硬编码 0 代替实测违规数"
        )
    violations = security.get("违规条数")
    if violations is None:
        raise MissingReportError(
            "benchmark 报告安全聚合缺少违规条数字段；缺字段按 NOT_VERIFIED 处理，"
            "不得填 0 冒充实测（006 G02）"
        )
    if cost is None:
        raise MissingReportError(
            "benchmark 报告缺少成功业务成本字段；缺字段按 NOT_VERIFIED 处理，"
            "不得填 0.0 冒充实测（006 G02）"
        )

    coverage = None
    for key in ("coverage", "branch_coverage", "覆盖率"):
        node = branch.get(key)
        if isinstance(node, dict):
            coverage = node.get("branch_coverage") or node.get("分支覆盖率")
        elif node is not None:
            coverage = node
        if coverage is not None:
            break

    # 稳定性：缺报告或缺字段一律抛错，由调用方归入 NOT_VERIFIED——
    # 不再用 availability=1.0 / error_rate=0.0 的理想值兜底（006 G02）。
    if stability is None:
        raise MissingReportError(
            "缺少 stability 报告：可用性/错误率无法判定，联合门禁按"
            " NOT_VERIFIED 处理，不填理想值（006 G02）"
        )
    availability = stability.get("availability")
    error_rate = stability.get("error_rate")
    if availability is None or error_rate is None:
        raise MissingReportError(
            "stability 报告缺少 availability 或 error_rate 字段，"
            "不得以默认值替代实测（006 G02）"
        )

    return {
        "qualified_rate": rate / 100.0,
        "e2e_p90_ms": e2e,
        "branch_coverage": coverage,
        "security_violations": violations,
        "cost_per_business": cost,
        "availability": availability,
        "error_rate": error_rate,
    }
