"""基准测试脚本：单笔业务冷启动 / 热启动 / 重复运行。

运行前提：服务已启动（默认 http://127.0.0.1:8000，LLM_PROVIDER=mock 无需密钥）。
用法：python -m tests.performance.benchmark --samples 20 --slo-ms 15000
输出：reports/<batch_id>.json（含逐笔业务轨迹），可被 build_report.py 汇总。
口径：
- Goodput = 合格业务数 / 实测墙钟时长（业务/秒），duration 为循环起止实测值。
- 报告根目录使用 batch_id；每笔业务记录服务端 run_id 与终态，
  业务 run_id 不再与批次 ID 混用（共享 Harness 设计说明 第 7.3 节）。
- v2 判定消费服务端 status：status=completed 才算流程完成。
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
    aggregate,
    classify,
    failed_business,
    load_tickets,
)
from src.utils.version import new_run_id  # noqa: E402


def flat_query(client: httpx.Client, query: str, ticket_id: str, batch_id: str) -> dict:
    resp = client.post(
        f"{client.base_url}/api/v1/chat/complete",
        json={
            "user_query": query,
            "task_id": ticket_id,
            "batch_id": batch_id,
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _fmt(value, suffix="ms", digits=1) -> str:
    """空指标不参与数值格式化（设计第 7.3 节）。"""
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return "n/a"


def run(args) -> dict:
    tickets = load_tickets(Path(args.data))
    samples = args.samples if args.samples else len(tickets)
    samples = min(samples, len(tickets))
    batch_id = new_run_id()
    businesses = []

    # 实测墙钟时长：覆盖整个采样循环（含请求间隔），不使用估算值
    t0 = time.perf_counter()
    with httpx.Client(base_url=args.server) as client:
        for i in range(samples):
            ticket = tickets[i % len(tickets)]
            try:
                data = flat_query(client, ticket.user_query, ticket.ticket_id, batch_id)
            except Exception as exc:  # noqa: BLE001
                # 请求层失败（连接错误/超时/非 2xx）记为失败业务，不中断采样
                businesses.append(failed_business(ticket, str(exc)))
                print(f"[{i+1:03d}/{samples}] {ticket.ticket_id} 请求失败: {exc}")
                continue
            metrics = data.get("metrics", {})
            business = classify(
                ticket,
                data.get("response", ""),
                metrics,
                slo_ms=args.slo_ms,
                status=data.get("status"),
            )
            # 逐笔保存服务端身份、终态与证据类型
            business.update({
                "run_id": data.get("run_id", ""),
                "task_id": data.get("task_id", ""),
                "batch_id": data.get("batch_id", ""),
                "status": data.get("status"),
                "stop_reason": data.get("stop_reason", ""),
                "evidence_kind": metrics.get("evidence_kind"),
            })
            businesses.append(business)
            print(
                f"[{i+1:03d}/{samples}] {ticket.ticket_id} "
                f"status={data.get('status')} "
                f"e2e={_fmt(metrics.get('e2e_ms'))} "
                f"ttft={_fmt(metrics.get('ttft_ms'))}"
            )
    duration_s = time.perf_counter() - t0

    agg = aggregate(businesses, duration_s=duration_s, cost_per_1k_tokens=args.cost_per_1k)
    payload = {
        "batch_id": batch_id,
        "schema_version": 2,
        "test_type": "benchmark",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "samples": samples,
        "duration_s": round(duration_s, 3),
        "aggregate": agg,
        "businesses": businesses,
    }
    out = Path(args.outdir) / f"{batch_id}.json" if args.outdir else (
        PROJECT_ROOT / "reports" / f"{batch_id}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"基准测试完成：{out}")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-Agent 基准测试")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--samples", type=int, default=20, help="采样业务数（≤ 工单数）")
    parser.add_argument("--slo-ms", type=float, default=15000.0)
    parser.add_argument(
        "--cost-per-1k",
        type=float,
        default=2.0,
        help="token 单价（元/千 token，mock 假设值）",
    )
    parser.add_argument("--outdir", default="")
    run(parser.parse_args())
    sys.exit(0)


if __name__ == "__main__":
    main()
