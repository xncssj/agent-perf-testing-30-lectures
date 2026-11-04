"""审计与内容最小化（006 契约 §6）：字段白名单与安全摘要。

集中观测（Langfuse 载荷）与结构化日志共用同一最小字段边界：
- 默认只导出结构化摘要（长度区间、类别、状态、计数），不导出原文；
- 白名单在构造载荷与写日志前生效——截断、只选最后一条消息或事后
  在看板隐藏字段都不构成脱敏；
- 需要复核具体事实时，由受控实验产物和逐笔 EvaluationRecord 提供
  证据位置及脱敏核查结论，不把明文或可猜测的普通哈希当作安全摘要；
- 缺审计事件时按 NOT_VERIFIED 处理，不把"未记录违规"写成"无违规"。

摘要函数对任何输入都只输出白名单内的结构化字段；新增字段必须先
更新本模块与其单元测试（合成敏感样本覆盖），再进入导出路径。
"""
from __future__ import annotations

from typing import Any

# 审计规则版本：摘要字段口径或白名单变化时递增
AUDIT_RULES_VERSION = "audit-v1"


def length_bucket(text: Any) -> str:
    """把任意文本折算为长度区间，杜绝原文外泄。"""
    n = len(text) if isinstance(text, str) else 0
    if n == 0:
        return "empty"
    if n <= 50:
        return "≤50"
    if n <= 200:
        return "51-200"
    if n <= 800:
        return "201-800"
    return ">800"


def summarize_input(query: Any, intent: str = "", accepted: bool = True) -> dict:
    """用户输入摘要：运行身份之外的接收与校验状态（契约 §6 表）。"""
    return {
        "length_bucket": length_bucket(query),
        "intent": intent or None,
        "accepted": bool(accepted),
    }


def summarize_tool_request(tool: str, args: Any) -> dict:
    """工具请求摘要：工具名与参数键集；不含参数值。"""
    keys = sorted(args.keys()) if isinstance(args, dict) else []
    return {"tool": tool, "arg_keys": keys, "rejected": False}


def summarize_tool_result(
    tool: str,
    data: Any,
    error: str | None,
    error_kind: str | None = None,
    duration_ms: float | None = None,
) -> dict:
    """工具结果摘要：命中与执行状态、错误类别、耗时；不含返回正文。"""
    status = "error" if error else "ok"
    found = None
    if not error and isinstance(data, dict) and "found" in data:
        found = bool(data.get("found"))
    return {
        "tool": tool,
        "status": status,
        "found": found,
        "error_kind": error_kind,
        "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
    }


def summarize_answer(
    response: Any,
    stop_reason: str = "",
    guardrail: dict | None = None,
) -> dict:
    """最终答复摘要：终态、停止原因、长度区间与护栏判定；不含答复原文。"""
    violations = None
    blocked = None
    if isinstance(guardrail, dict):
        raw = guardrail.get("violations")
        violations = len(raw) if isinstance(raw, list) else raw
        blocked = bool(guardrail.get("blocked"))
    return {
        "length_bucket": length_bucket(response),
        "stop_reason": stop_reason or None,
        "guardrail": {"blocked": blocked, "violations": violations},
    }


def audit_event(
    run_id: str,
    step_id: str,
    event_type: str,
    decision: str,
    **fields: Any,
) -> dict:
    """构造一条审计事件：运行身份、步骤身份、事件类型、决策结果与规则版本。

    审计覆盖输入接收、工具请求/结果、护栏判定与最终答复（契约 §6）。
    fields 只接受调用方显式给出的白名单摘要（由 summarize_* 产出）。
    """
    return {
        "rule_version": AUDIT_RULES_VERSION,
        "run_id": run_id,
        "step_id": step_id,
        "event_type": event_type,
        "decision": decision,
        **fields,
    }
