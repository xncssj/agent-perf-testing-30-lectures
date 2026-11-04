"""稳定性测试脚本：固定并发长时间运行，观察错误率、时延漂移与内存风险。

用法：python -m tests.performance.stability --concurrency 20 --duration 120 \
           --slo-ms 15000
输出：reports/stability_<run_id>.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.performance.common import (  # noqa: E402
    SHARED_DATA_DIR,
    aggregate,
    failed_business,
    load_tickets,
    classify,
)

_STOP = threading.Event()


def _worker(client: httpx.Client, tickets, slo_ms: float, results: list, idx: int,
            batch_id: str):
    i = idx
    while not _STOP.is_set():
        ticket = tickets[i % len(tickets)]
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
        except Exception as exc:  # noqa: BLE001
            business = failed_business(ticket, str(exc))
        results.append(business)
        i += 1


def run(args) -> dict:
    tickets = load_tickets(args.data)
    batch_id = f"stability_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    results = []

    with httpx.Client(base_url=args.server) as client:
        threads = [
            threading.Thread(target=_worker, args=(client, tickets, args.slo_ms, results, i, batch_id))
            for i in range(args.concurrency)
        ]
        for t in threads:
            t.start()
        time.sleep(args.duration)
        _STOP.set()
        for t in threads:
            t.join(timeout=30)

    agg = aggregate(results, duration_s=args.duration)
    agg["运行时长(秒)"] = args.duration
    payload = {
        "batch_id": batch_id,
        "schema_version": 2,
        "test_type": "stability",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "business_count": len(results),
        "aggregate": agg,
        "businesses": results,
    }
    out = PROJECT_ROOT / "reports" / f"{batch_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"稳定性测试完成：{out} 业务数={len(results)}")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Agent 稳定性测试")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=120.0, help="稳定性运行秒数")
    parser.add_argument("--slo-ms", type=float, default=15000.0)
    run(parser.parse_args())
    sys.exit(0)


if __name__ == "__main__":
    main()