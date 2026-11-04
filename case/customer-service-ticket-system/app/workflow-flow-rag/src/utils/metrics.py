"""指标计算：分位数、Goodput、业务判定与聚合输出。

口径对齐课程第 04、08 讲：
- 分位数：P50 / P90 / P99 用于表达分布而非平均值。
- 业务 Goodput = 满足 SLO 的合格业务数 ÷ 计费业务数（单位时间内合格吞吐）。
- 业务判定沿用课程第 01 讲四级判定：请求成功 → 流程完成 → 业务完成 → 结果合格，
  并叠加时延（E2E/首字）与安全（护栏）两层（第 04、19 讲口径，课程正文对应段落）。
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path
from typing import Iterable

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.guardrail import violation_count  # noqa: E402


def describe(values: Iterable[float]) -> dict:
    """计算样本的集中趋势与分位数，无数据时返回空字典。"""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {}
    vals.sort()
    return {
        "count": len(vals),
        "mean": round(statistics.fmean(vals), 3),
        "p50": round(_percentile(vals, 50), 3),
        "p90": round(_percentile(vals, 90), 3),
        "p99": round(_percentile(vals, 99), 3),
        "min": round(vals[0], 3),
        "max": round(vals[-1], 3),
        "std": round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0,
    }


def _percentile(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def goodput_throughput(
    total: int,
    qualified: int,
    duration_s: float,
) -> float:
    """业务 Goodput = 合格业务数 / 时长（单位：业务/秒）。

    合格业务 = 请求成功 且 流程完成 且 业务完成 且 结果合格 且 时延满足 SLO。
    """
    if duration_s <= 0:
        return 0.0
    return round(qualified / duration_s, 4)


def business_levels(
    request_ok: bool,
    flow_done: bool,
    business_done: bool,
    result_ok: bool,
    latency_ms: float,
    slo_ms: float | None = None,
    first_answer_ms: float | None = None,
    first_answer_slo_ms: float | None = None,
    guardrail: dict | None = None,
) -> dict:
    """业务判定：四级判定 + 时延（E2E/首字）+ 安全（护栏）三层，返回逐级结论。

    双指标时延口径（v2 流式，课程正文对应段落）：完整答复 E2E 对齐 slo_ms，
    首字对齐 first_answer_slo_ms（TTFT 口径）。首字实测缺失（如 v1 入口）
    时该项不参与判定，first_answer_ok 为 None 表示不适用。

    安全层口径（课程正文对应段落）：guardrail 结果存在时
    security_ok = (违规条数 == 0)；缺失时记 None（不适用），不冒充"无违规"，
    聚合侧单独统计缺失笔数。
    """
    levels = {
        "request_ok": request_ok,
        "flow_done": flow_done,
        "business_done": business_done,
        "result_ok": result_ok,
    }
    slo_ok = (latency_ms <= slo_ms) if slo_ms is not None else True
    first_answer_ok: bool | None = None
    if first_answer_slo_ms is not None and first_answer_ms is not None:
        first_answer_ok = first_answer_ms <= first_answer_slo_ms
        slo_ok = slo_ok and first_answer_ok
    levels["slo_ok"] = slo_ok
    levels["first_answer_ok"] = first_answer_ok
    if guardrail is None:
        security_ok: bool | None = None
    else:
        security_ok = violation_count(guardrail) == 0
    levels["security_ok"] = security_ok
    levels["qualified"] = (
        request_ok and flow_done and business_done and result_ok and slo_ok
        and security_ok is not False
    )
    return levels