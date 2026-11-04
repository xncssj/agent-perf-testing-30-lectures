"""扇出（Fan-out）测试：观测 Multi-Agent 并行调度效率与路径正确性。

运行前提：服务已启动（LLM_PROVIDER=mock，无需故障注入）。
用法：python -m tests.performance.fanout --samples 30 --slo-ms 15000

采集口径（read 侧取自 /complete 返回的 trace）：
- round_wall_ms：一轮并行扇出的墙钟（所有子 Agent 的最大完成耗时）
- serial_sum_ms：串行执行各子 Agent 的耗时之和
- 并行效率 = serial_sum / round_wall（>1 说明并行优于串行）
- 路径正确性 agent_path_ok：实际编排 Agent 覆盖工单 expected_agents

输出：reports/fanout_<时间戳>.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
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


def probes(client: httpx.Client, ticket, slo_ms: float, batch_id: str = "") -> dict:
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
        business["fanout"] = trace.get("fanout", {})
        business["path_ok"] = agent_path_ok(ticket, metrics)
        return business
    except Exception as exc:  # noqa: BLE001
        return failed_business(ticket, str(exc))


def run(args) -> dict:
    tickets = load_tickets(args.data)
    samples = min(args.samples, len(tickets))
    batch_id = f"fanout_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    businesses = []
    with httpx.Client(base_url=args.server) as client:
        for i in range(samples):
            ticket = tickets[i % len(tickets)]
            business = probes(client, ticket, slo_ms=args.slo_ms, batch_id=batch_id)
            businesses.append(business)
            fan = business.get("fanout", {})
            print(
                f"[{i+1:03d}/{samples}] {business['ticket_id']} "
                f"type={business.get('expected_intent', '')[:12]:<12} "
                f"path={business.get('path_ok')} "
                f"eff={fan.get('efficiency', 0):.2f} "
                f"wall={fan.get('round_wall_ms', 0):.1f}ms "
                f"serial={fan.get('serial_sum_ms', 0):.1f}ms"
            )

    effs = [b.get("fanout", {}).get("efficiency", 0) for b in businesses if b.get("fanout")]
    walls = [b.get("fanout", {}).get("round_wall_ms", 0) for b in businesses if b.get("fanout")]
    serials = [b.get("fanout", {}).get("serial_sum_ms", 0) for b in businesses if b.get("fanout")]
    path_ok = sum(1 for b in businesses if b.get("path_ok"))
    fanout_agg = {
        "并行效率": {
            "mean": round(sum(effs) / len(effs), 4) if effs else 0.0,
            "min": round(min(effs), 4) if effs else 0.0,
            "max": round(max(effs), 4) if effs else 0.0,
        },
        "扇出墙钟round_wall_ms": {
            "mean": round(sum(walls) / len(walls), 3) if walls else 0.0,
        },
        "串行和serial_sum_ms": {
            "mean": round(sum(serials) / len(serials), 3) if serials else 0.0,
        },
        "路径正确业务": f"{path_ok}/{len(businesses)}",
        "降级观测": describe_degraded(businesses),
    }
    print(json.dumps(fanout_agg, ensure_ascii=False, indent=2))

    run_id = f"fanout_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "run_id": run_id,
        "test_type": "fanout",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "samples": samples,
        "fanout": fanout_agg,
        "businesses": businesses,
    }
    out = PROJECT_ROOT / "reports" / f"{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"扇出测试完成：{out}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Agent 扇出测试")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--slo-ms", type=float, default=15000.0)
    run(parser.parse_args())
    sys.exit(0)


if __name__ == "__main__":
    main()