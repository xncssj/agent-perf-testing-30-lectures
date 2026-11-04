"""级联故障测试：观测单子 Agent 故障是否拖垮其他业务与整体服务。

推荐启动方式（快速复现）：
    AGENT_FAULT_ENABLED=true AGENT_FAULT_TARGET=logistics_agent \
    AGENT_FAULT_MODE=error AGENT_MAX_RETRIES=1 AGENT_BREAKER_THRESHOLD=3 \
    uvicorn src.main:app --port 8000

注意：AGENT_FAULT_ENABLED 必须显式置 true；只设 TARGET/MODE 时故障默认关闭，
脚本会跑出"全部合格、零降级"的假阴性结论。

对 logistics_agent 注入持续故障：
- 受影响工单（物流类）：订单/物流数据链断裂 → 业务降级（结果不合格或熔断跳过）
- 受影响工单内部其他子 Agent（order_agent）：应保持成功 → 无内部级联
- 不受影响工单（订单/知识类）：应保持合格 → 无业务级联（故障隔离）

同时验证：连续失败达到熔断阈值后，后续调用被降级跳过（skipped_breaker），
不消耗 LLM 调用（断路器防线）。

用法：python -m tests.performance.cascade --target logistics_agent \
           --affected 12 --unaffected 12 --slo-ms 15000
输出：reports/cascade_<时间戳>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.performance.common import (  # noqa: E402
    SHARED_DATA_DIR,
    agent_path_ok,
    describe_degraded,
    failed_business,
    load_tickets,
    classify,
)

# 子 Agent -> 其工具
AGENT_TOOL = {
    "order_agent": "query_order",
    "logistics_agent": "query_logistics",
    "knowledge_agent": "search_knowledge",
}


def _affected(tickets, target_agent: str):
    target_tool = AGENT_TOOL.get(target_agent, "")
    return [
        t
        for t in tickets
        if target_agent in (t.expected_agents or []) and target_tool in (t.expected_tools or [])
    ]


def _unaffected(tickets, target_agent: str):
    target_tool = AGENT_TOOL.get(target_agent, "")
    return [
        t
        for t in tickets
        if target_agent not in (t.expected_agents or [])
        and target_tool not in (t.expected_tools or [])
        and (t.expected_tools or [])
    ]


def _send(client, ticket, slo_ms, batch_id: str = ""):
    try:
        resp = client.post(
            "/api/v1/chat/complete",
            json={
                "user_query": ticket.user_query,
                "task_id": ticket.ticket_id,
                "batch_id": batch_id,
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        metrics = data.get("metrics", {})
        business = classify(
            ticket, data.get("response", ""), metrics, slo_ms,
            status=data.get("status"),
        )
        business.update({
            "run_id": data.get("run_id", ""),
            "task_id": data.get("task_id", ""),
            "batch_id": data.get("batch_id", ""),
            "status": data.get("status"),
            "stop_reason": data.get("stop_reason", ""),
            "evidence_kind": metrics.get("evidence_kind"),
        })
        trace = metrics.get("trace", {})
        business["path_ok"] = agent_path_ok(ticket, metrics)
        business["degraded"] = trace.get("degraded", False)
        business["agent_status"] = {
            a.get("name"): a.get("status") for a in trace.get("agents", [])
        }
        return business
    except Exception as exc:  # noqa: BLE001
        return failed_business(ticket, str(exc))


def _aggregate_status(businesses, key: str) -> dict:
    ok = sum(1 for b in businesses if b["levels"].get(key.replace("qualified", "qualified")))
    return {
        key: ok,
        f"{key}百分比": round(ok / len(businesses) * 100, 2) if businesses else 0.0,
    }


def run(args) -> dict:
    batch_id = f"cascade_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tickets = load_tickets(args.data)
    affected = _affected(tickets, args.target)[: args.affected]
    unaffected = _unaffected(tickets, args.target)[: args.unaffected]
    print(
        f"目标 Agent: {args.target}  受影响工单={len(affected)} "
        f"不受影响工单={len(unaffected)}"
    )
    if not affected or not unaffected:
        print("工单样本不足，请调整 --affected / --unaffected")
        sys.exit(2)

    aff_b, unaff_b = [], []
    with httpx.Client(base_url=args.server) as client:
        for ticket in affected:
            b = _send(client, ticket, args.slo_ms, batch_id)
            aff_b.append(b)
            print(
                f"[受影响] {b['ticket_id']} {b.get('expected_intent', '')[:10]} "
                f"合格={b['levels'].get('qualified')} 降级={b.get('degraded')} "
                f"agents={b.get('agent_status', {})}"
            )
        for ticket in unaffected:
            b = _send(client, ticket, args.slo_ms, batch_id)
            unaff_b.append(b)
            print(
                f"[不受影响] {b['ticket_id']} {b.get('expected_intent', '')[:10]} "
                f"合格={b['levels'].get('qualified')} 降级={b.get('degraded')}"
            )

    # 受影响工单内：不相关子 Agent 是否被拖垮（内部级联观测）。
    # 目标是“目标 Agent 之外的同业务兄弟 Agent”，典型如 order_agent：
    # 物流 Agent 故障时，订单 Agent 不应被一起拖垮。
    other_success = 0
    other_total = 0
    for b in aff_b:
        for name, status in (b.get("agent_status") or {}).items():
            if name == args.target:
                continue
            other_total += 1
            if status in ("success", "no_tool"):
                other_success += 1

    report = {
        "目标Agent": args.target,
        "受影响工单": {
            "数量": len(aff_b),
            "合格率": _aggregate_status(aff_b, "qualified"),
            "降级观测": describe_degraded(aff_b),
            "路径正确率": round(
                sum(1 for b in aff_b if b.get("path_ok")) / len(aff_b) * 100, 2
            ),
        },
        "不受影响工单(隔离验证)": {
            "数量": len(unaff_b),
            "合格率": _aggregate_status(unaff_b, "qualified"),
            "降级观测": describe_degraded(unaff_b),
        },
        "内部门控Agent未级联": {
            "观测调用数": other_total,
            "成功数": other_success,
            "成功率": round(other_success / other_total * 100, 2) if other_total else None,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    run_id = f"cascade_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "run_id": run_id,
        "test_type": "cascade",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "report": report,
        "affected_businesses": aff_b,
        "unaffected_businesses": unaff_b,
    }
    out = PROJECT_ROOT / "reports" / f"{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"级联故障测试完成：{out}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Agent 级联故障测试")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--target", default="logistics_agent", help="注入故障的子 Agent")
    parser.add_argument("--affected", type=int, default=12)
    parser.add_argument("--unaffected", type=int, default=12)
    parser.add_argument("--slo-ms", type=float, default=15000.0)
    run(parser.parse_args())
    sys.exit(0)


if __name__ == "__main__":
    main()