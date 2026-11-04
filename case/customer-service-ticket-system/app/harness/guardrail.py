"""安全护栏：对生成答复做敏感信息与越界内容检查（三阶段共享实现）。

课程第 19 讲"安全门禁"的最小实现：答复中出现以下任一模式即记为违规，
可能被门禁拦截。压测时通过合成违规答复验证安全门禁能否拦截。

由阶段三实施时从其 src/workflow/guardrail.py 抽取到共享包（单一事实源，
课程跨案例共享代码），阶段一/二按同一实现采集
逐笔安全状态，阶段三保留薄垫片转发。
"""
from __future__ import annotations

import re

_SENSITIVE_PATTERNS = [
    (r"\b[1-9]\d{17}\b", "身份证号"),
    (r"\b1[3-9]\d{9}\b", "手机号"),
    (r"\b[456]\d{3}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", "银行卡号"),
    (r"ignore previous instructions|system prompt|直接输出|忽略以上", "指令注入模板"),
]

# 下游系统禁止透传的异常键（红线上报）
_SYSTEM_BLOCKED = {"数据库连接串", "内网地址"}


class Guardrail:
    """答复安全检查器。"""

    def check(self, text: str) -> dict:
        violations: list[dict] = []
        for pattern, label in _SENSITIVE_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                violations.append({"type": "sensitive", "label": label, "rule": pattern})
        for token in _SYSTEM_BLOCKED:
            if token in text:
                violations.append({"type": "system_blocked", "label": token})
        blocked = bool(violations)
        return {"violations": violations, "blocked": blocked}


guardrail = Guardrail()


def violation_count(gate_result: dict | None) -> int:
    """从护栏结果中取违规条数；缺失或形状异常时返回 0 条（缺数据不冒充违规）。"""
    if not isinstance(gate_result, dict):
        return 0
    return len(gate_result.get("violations") or [])
