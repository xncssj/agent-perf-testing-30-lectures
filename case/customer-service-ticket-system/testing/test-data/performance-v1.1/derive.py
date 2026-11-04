"""v1.1 派生规则：v1 用例单 → v1.1 黄金用例。

派生只做两件事，源档（performance-v1/cases.json）保持历史冻结：
1. `rule_version` 升为 `agent-performance-rule-v1.1`；
2. AP-MUL-05~08 四场景故障参数差异化（2026-11-03 阶段二三真实模型轮 B4）。

B4 背景：v1 中 AP-MUL-06/07/08 的 `required_env` 完全相同（logistics_agent +
error + breaker 1），四个故障场景无法区分。v1.1 按测试方案 §8.5 改为：
- 05：物流超时（timeout，熔断阈值 5，不重试）；
- 06：物流持续错误 + `AGENT_FAULT_LIMIT=3` 自愈（阈值 2，先熔断后自愈）；
- 07：订单子 Agent 不可用（down，隔离到订单类工单）；
- 08：共享 LLM 超时（llm，波及全部专业 Agent）。

同时为每场景补 `judge_boundary`：故障窗内不再要求答复命中不可核实的物流事实，
改为「已核实事实必须命中 + 必须明确声明未核实」，修 09-27 因缺「物流未核实」判 FAIL 的边界缺陷。
"""
from __future__ import annotations

import copy

RULE = "agent-performance-rule-v1.1"

UNVERIFIED_PHRASES = ["未核实", "无法核实", "未确认", "无法确认"]

FAULT_OVERRIDES = {
    "AP-MUL-05": {
        "fault_profile": "agent_timeout",
        "scenario_id": "timeout-logistics",
        "required_env": {
            "AGENT_FAULT_ENABLED": "true",
            "AGENT_FAULT_TARGET": "logistics_agent",
            "AGENT_FAULT_MODE": "timeout",
            "AGENT_FAULT_LATENCY_MS": "1000",
            "AGENT_MAX_RETRIES": "0",
            "AGENT_BREAKER_THRESHOLD": "5",
            "AGENT_DEGRADATION": "true",
        },
        "expected_trace": {
            "degraded": True,
            "degraded_agent": "logistics_agent",
            "agent_status": "timeout",
        },
        "expected_outcome": "仅陈述已核实的订单事实，并明确物流未核实；不能给出未核实的当前位置或预计送达。",
        "judge_boundary": {
            "verified_facts": ["order_id", "status"],
            "unverified_phrases": UNVERIFIED_PHRASES,
        },
    },
    "AP-MUL-06": {
        "fault_profile": "agent_timeout",
        "scenario_id": "error-self-heal",
        "required_env": {
            "AGENT_FAULT_ENABLED": "true",
            "AGENT_FAULT_TARGET": "logistics_agent",
            "AGENT_FAULT_MODE": "error",
            "AGENT_FAULT_LATENCY_MS": "0",
            "AGENT_MAX_RETRIES": "0",
            "AGENT_BREAKER_THRESHOLD": "2",
            "AGENT_FAULT_LIMIT": "3",
            "AGENT_DEGRADATION": "true",
        },
        "expected_trace": {
            "degraded": True,
            "degraded_agent": "logistics_agent",
            "agent_status": "error",
            "breaker_opens_after_faults": 2,
            "self_heal_after_faults": 3,
        },
        "expected_outcome": "注入前段按降级边界答复（同 05）；熔断打开后跳过物流子 Agent；自愈后按完整事实答复。",
        "judge_boundary": {
            "verified_facts": ["order_id", "status"],
            "unverified_phrases": UNVERIFIED_PHRASES,
        },
    },
    "AP-MUL-07": {
        "fault_profile": "breaker",
        "scenario_id": "order-agent-down",
        "required_env": {
            "AGENT_FAULT_ENABLED": "true",
            "AGENT_FAULT_TARGET": "order_agent",
            "AGENT_FAULT_MODE": "down",
            "AGENT_FAULT_LATENCY_MS": "0",
            "AGENT_MAX_RETRIES": "0",
            "AGENT_BREAKER_THRESHOLD": "1",
            "AGENT_DEGRADATION": "true",
        },
        "expected_trace": {
            "degraded": True,
            "degraded_agent": "order_agent",
            "agent_status": "error",
        },
        "expected_outcome": "明确声明订单信息未核实；可陈述物流侧已核实信息，但不得编造订单状态。",
        "judge_boundary": {
            "verified_facts": ["order_id"],
            "unverified_phrases": UNVERIFIED_PHRASES,
        },
    },
    "AP-MUL-08": {
        "fault_profile": "breaker",
        "scenario_id": "llm-timeout",
        "required_env": {
            "AGENT_FAULT_ENABLED": "true",
            "AGENT_FAULT_TARGET": "llm",
            "AGENT_FAULT_MODE": "timeout",
            "AGENT_FAULT_LATENCY_MS": "1500",
            "AGENT_MAX_RETRIES": "0",
            "AGENT_BREAKER_THRESHOLD": "3",
            "AGENT_DEGRADATION": "true",
        },
        "expected_trace": {
            "degraded": True,
            "degraded_agent": "llm",
            "agent_status": "timeout",
            "affected_agents": ["order_agent", "logistics_agent"],
        },
        "expected_outcome": "模型侧故障期间明确声明订单与物流均未核实；不得编造任何事实。",
        "judge_boundary": {
            "verified_facts": ["order_id"],
            "unverified_phrases": UNVERIFIED_PHRASES,
        },
    },
}

# 故障窗补测变体（2026-11-03 W1B，非黄金用例，仅供 fault08b-stage2.json 派生档；
# 由派生脚本按本表覆写，validate_fault_scenarios_v1_1 不校验——该函数只查黄金四档）。
# 口径：AGENT_FAULT_MODE 枚举只有 timeout/error/down，无限流形态；按老板拍板用
# error + TARGET=llm 持续拒绝近似限流（429 语义），重试→熔断→降级行为链完整。
FAULT_OVERRIDE_VARIANTS = {
    "AP-MUL-08": {
        "fault_profile": "breaker",
        "scenario_id": "llm-reject-approx",
        "required_env": {
            "AGENT_FAULT_ENABLED": "true",
            "AGENT_FAULT_TARGET": "llm",
            "AGENT_FAULT_MODE": "error",
            "AGENT_FAULT_LATENCY_MS": "0",
            "AGENT_MAX_RETRIES": "0",
            "AGENT_BREAKER_THRESHOLD": "3",
            "AGENT_DEGRADATION": "true",
        },
        "expected_trace": {
            "degraded": True,
            "degraded_agent": "llm",
            "agent_status": "error",
            "affected_agents": ["order_agent", "logistics_agent"],
        },
        "expected_outcome": "限流近似（llm 持续拒绝）期间明确声明订单与物流均未核实；不得编造任何事实。",
        "judge_boundary": {
            "verified_facts": ["order_id"],
            "unverified_phrases": UNVERIFIED_PHRASES,
        },
    },
}


def apply_fault_override(case: dict, overrides: dict | None = None) -> dict:
    """按 case_id 覆盖故障场景；无覆盖的用例原样返回。

    ``overrides`` 缺省用黄金档 ``FAULT_OVERRIDES``；传 ``FAULT_OVERRIDE_VARIANTS``
    时生成补测变体（仅 fault08b 派生档使用，不进黄金用例）。
    """
    override = (overrides or FAULT_OVERRIDES).get(
        case.get("case_id") or case.get("source_case_id"))
    if not override:
        return case
    reference = case.setdefault("reference", {})
    scenario = reference.setdefault("fault_scenario", {})
    scenario["scenario_id"] = override["scenario_id"]
    scenario["required_env"] = copy.deepcopy(override["required_env"])
    scenario["expected_trace"] = copy.deepcopy(override["expected_trace"])
    scenario["expected_outcome"] = override["expected_outcome"]
    scenario["judge_boundary"] = copy.deepcopy(override["judge_boundary"])
    tags = case.setdefault("perf_tags", {})
    tags["fault_profile"] = override["fault_profile"]
    return case


def derive_case(case: dict) -> dict:
    """派生单条 v1.1 用例（深拷贝，不改源档）。"""
    item = copy.deepcopy(case)
    item["rule_version"] = RULE
    return apply_fault_override(item)


def derive_cases(cases: list[dict]) -> list[dict]:
    """派生整档 v1.1 用例。"""
    return [derive_case(row) for row in cases]
