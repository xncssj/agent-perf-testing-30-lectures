# 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。
"""容量测试脚本：阶梯加压，观察 1→N 并发下的时延、合格率与 Goodput。

用法：python -m tests.performance.capacity --levels 1 5 10 20 30 50 \
           --per-level 20 --slo-ms 15000
输出：reports/capacity_<run_id>.json
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
    aggregate,
    classify,
    failed_business,
    load_tickets,
)


def _worker(client: httpx.Client, query: str, slo_ms: float, ticket, batch_id: str) -> dict:
    try:
        resp = client.post(
            "/api/v1/chat/complete",
            json={
                "user_query": query,
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
        # 逐笔保留服务端 trace（含工具调用与 attempts），供第 12、13 讲
        # 采集"重试增加"信号与调用放大系数（待办 1）
        "trace": data.get("trace", {}),
        })
        return business
    except Exception as exc:  # noqa: BLE001
        return failed_business(ticket, str(exc))


def run(args) -> dict:
    tickets = load_tickets(args.data)
    levels = args.levels
    per_level = args.per_level
    batch_id = f"capacity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    level_results = []

    with httpx.Client(base_url=args.server) as client:
        for level in levels:
            businesses = []
            t0 = time.perf_counter()
            with cf.ThreadPoolExecutor(max_workers=level) as pool:
                futures = []
                for _ in range(per_level):
                    ticket = tickets[(_ ) % len(tickets)]
                    futures.append(pool.submit(_worker, client, ticket.user_query, args.slo_ms, ticket, batch_id))
                for f in cf.as_completed(futures):
                    businesses.append(f.result())
            duration = time.perf_counter() - t0
            agg = aggregate(businesses, duration_s=duration)
            # 逐笔轨迹随梯度一并落盘，供事后回溯（此前仅保存汇总导致轨迹不可回溯）
            level_results.append(
                {"concurrency": level, "duration_s": round(duration, 3), **agg, "businesses": businesses}
            )
            print(
                f"并发={level:>3} 耗时={duration:6.2f}s "
                f"E2E P90={agg['时延指标(mm)']['E2E']['p90']:>8.1f}ms "
                f"合格={agg['合格率']['合格业务百分比']:>5.2f}% "
                f"Goodput={agg['业务Goodput(合格业务/秒)']}"
            )

    payload = {
        "batch_id": batch_id,
        "schema_version": 2,
        "test_type": "capacity",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "levels": level_results,
    }
    out = PROJECT_ROOT / "reports" / f"{batch_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"容量测试完成：{out}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Single Agent 容量测试（阶梯加压）")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--levels", type=int, nargs="+", default=[1, 5, 10, 20, 30, 50])
    parser.add_argument("--per-level", type=int, default=20, help="每个梯度的业务数")
    parser.add_argument("--slo-ms", type=float, default=15000.0)
    run(parser.parse_args())
    sys.exit(0)


if __name__ == "__main__":
    main()