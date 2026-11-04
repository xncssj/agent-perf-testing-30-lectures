"""恢复测试：故障自愈后系统复稳（积压清空 + 二次加压 + 复稳判据）。

推荐启动方式（快速复现）：
    AGENT_FAULT_TARGET=logistics_agent AGENT_FAULT_MODE=error \
    AGENT_FAULT_LIMIT=6 AGENT_MAX_RETRIES=2 \
    uvicorn src.main:app --port 8000

对 logistics_agent 注入前 6 次故障后自动自愈：
- 阶段一 首轮加压：前几笔业务降级（不合格），随后系统自动恢复（无残留降级）
- 阶段二 二次加压：并发重新加压，合格率回到正常（复稳判据）

用法：python -m tests.performance.recovery --burst 20 --slo-ms 15000
输出：reports/recovery_<时间戳>.json
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
    describe_degraded,
    failed_business,
    load_tickets,
    classify,
)


def _affected_logistics(tickets):
    return [
        t for t in tickets
        if "logistics_agent" in (t.expected_agents or [])
        and "query_logistics" in (t.expected_tools or [])
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
        business["degraded"] = metrics.get("trace", {}).get("degraded", False)
        return business
    except Exception as exc:  # noqa: BLE001
        return failed_business(ticket, str(exc))


def _metric_value(client, name: str):
    """从 /metrics 读取单个 gauge 当前值；缺失或不可达返回 None。"""
    try:
        resp = client.get("/metrics", timeout=5)
        for line in resp.text.splitlines():
            if line.startswith(name + " ") or line.startswith(name + "{"):
                return float(line.rsplit(" ", 1)[-1])
    except Exception:  # noqa: BLE001
        return None
    return None


def run(args) -> dict:
    batch_id = f"recovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tickets = load_tickets(args.data)
    pool_tickets = _affected_logistics(tickets)
    if not pool_tickets:
        print("未找到物流类受影响工单")
        sys.exit(2)

    stage1 = []
    backlog_samples = []
    cleared_seconds = None
    with httpx.Client(base_url=args.server) as client:
        t0 = time.perf_counter()
        for i in range(args.burst):
            ticket = pool_tickets[i % len(pool_tickets)]
            business = _send(client, ticket, args.slo_ms, batch_id)
            business["seq"] = i
            stage1.append(business)
            print(
                f"阶段一[{i+1:03d}] {ticket.ticket_id} 合格={business['levels'].get('qualified')} "
                f"降级={business.get('degraded')} e2e={business['metrics'].get('e2e_ms', 0):.0f}ms"
            )
        stage1_duration = time.perf_counter() - t0

        # 复稳判据：末尾连续 5 笔无降级
        tail = [b for b in stage1 if b.get("degraded")][-3:]
        recovered_after = next(
            (
                i for i in range(len(stage1))
                if all(not b.get("degraded") for b in stage1[i:])
            ),
            None,
        )

        # 阶段二 二次加压：并发请求验证复稳
        stage2 = []
        t1 = time.perf_counter()
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for i in range(args.burst * 2):
                ticket = pool_tickets[i % len(pool_tickets)]
                futures.append(pool.submit(_send, client, ticket, args.slo_ms, batch_id))
            for f in cf.as_completed(futures):
                stage2.append(f.result())
        stage2_duration = time.perf_counter() - t1
        print("阶段二 二次加压完成")

        # 积压清空观测（P2-17）：轮询在途业务与工具池在途至归零，记录清空耗时
        backlog_t0 = time.perf_counter()
        deadline = backlog_t0 + args.backlog_timeout_s
        while time.perf_counter() < deadline:
            runs = _metric_value(client, "agent_runs_inflight")
            tools = _metric_value(client, "harness_tool_calls_inflight")
            backlog_samples.append({
                "t_s": round(time.perf_counter() - backlog_t0, 2),
                "agent_runs_inflight": runs,
                "tool_calls_inflight": tools,
            })
            if runs == 0 and tools == 0:
                cleared_seconds = backlog_samples[-1]["t_s"]
                break
            time.sleep(args.backlog_poll_s)

    def _rate(bs):
        total = len(bs)
        qualified = sum(1 for b in bs if b["levels"]["qualified"])
        degraded = sum(1 for b in bs if b.get("degraded"))
        return round(qualified / total * 100, 2) if total else 0.0, degraded

    q1, d1 = _rate(stage1)
    q2, d2 = _rate(stage2)
    report = {
        "阶段一(首轮加压)": {
            "业务数": len(stage1),
            "合格率%": q1,
            "降级业务数": d1,
            "起始降级seq": [b["seq"] for b in stage1 if b.get("degraded")],
            "复稳起点seq": recovered_after,
            "末5笔降级数": sum(1 for b in stage1[-5:] if b.get("degraded")),
            "耗时s": round(stage1_duration, 3),
            "降级观测": describe_degraded(stage1),
        },
        "阶段二(二次加压复稳)": {
            "业务数": len(stage2),
            "合格率%": q2,
            "降级业务数": d2,
            "耗时s": round(stage2_duration, 3),
        },
        "复稳判据": "通过" if (q2 >= 95.0 and d2 == 0) else "未通过",
        "积压清空(P2-17)": {
            "起始采样": backlog_samples[0] if backlog_samples else None,
            "清空耗时s": cleared_seconds,
            "清空判定": "通过" if cleared_seconds is not None else "未清空(超时)",
            "采样数": len(backlog_samples),
            "采样序列": backlog_samples[:20],
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    run_id = f"recovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "run_id": run_id,
        "test_type": "recovery",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "report": report,
        "stage1": stage1,
        "stage2": stage2,
    }
    out = PROJECT_ROOT / "reports" / f"{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"恢复测试完成：{out}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Agent 恢复测试")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--burst", type=int, default=20)
    parser.add_argument("--slo-ms", type=float, default=15000.0)
    parser.add_argument("--backlog-timeout-s", type=float, default=60.0,
                        help="积压清空观测最长等待秒数（P2-17）")
    parser.add_argument("--backlog-poll-s", type=float, default=2.0,
                        help="积压清空观测轮询间隔秒数")
    run(parser.parse_args())
    sys.exit(0)


if __name__ == "__main__":
    main()