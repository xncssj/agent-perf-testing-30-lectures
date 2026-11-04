"""稳定性测试：固定并发持续压测，观测可用性/错误率/时延飘移。

可用性 = 成功业务(流程完成) / 总请求；错误率 = 失败请求 / 总请求。
稳定目标：可用性 ≥ 0.995，错误率 ≤ 0.005，且 E2E P95 相对基线漂移有限。
v2 口径：经 arun 入口并发提交（信号量限流），报告同时输出首字分位数。
"""
from __future__ import annotations

import asyncio
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402
from src.utils.metrics import describe  # noqa: E402
# harness 包位于案例根，需在 src 包导入（触发路径引导）之后引入
from harness.contracts import RunInput  # noqa: E402


async def run_all(settings, service, tickets, concurrency: int, rounds: int) -> list[dict]:
    """固定并发持续提交：信号量限流 + 全量任务排队（对齐原线程池语义）。"""
    from tests.performance.common import load_tickets  # noqa: F401

    qs = [t.user_query for t in tickets]
    gate = asyncio.Semaphore(concurrency)
    batch_id = f"stab_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    async def one(idx: int) -> dict:
        async with gate:
            r = await service.arun(RunInput(
                query=qs[idx % len(qs)], task_id=f"STAB#{idx}", batch_id=batch_id,
            ))
            return r.model_dump()

    return await asyncio.gather(*(one(i) for i in range(rounds * concurrency)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Workflow 稳定性测试（固定并发持续压测；W10 全时长可用 --duration 驱动）"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--duration", type=float, default=40.0,
                        help="持续秒数（默认 40s 缩短版；全时长按业务积累量÷Goodput 回填）")
    parser.add_argument("--data", help="工单文件路径（缺省 data_dir/tickets.json）")
    args = parser.parse_args(argv)

    settings = Settings()
    service = WorkflowService(settings)
    from tests.performance.common import load_tickets, stamp_and_write
    data_path = Path(args.data) if args.data else settings.data_dir / "tickets.json"
    tickets = load_tickets(data_path)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    concurrency = args.concurrency
    duration_s = args.duration
    rounds = max(1, int(duration_s))  # 每轮提交 concurrency 个请求，约等于持续 duration_s 秒

    started_at = datetime.now().isoformat(timespec="seconds")
    started = time.perf_counter()
    results_raw = asyncio.run(run_all(settings, service, tickets, concurrency, rounds))
    wall = time.perf_counter() - started
    try:
        asyncio.run(service.aclose())
    except Exception:  # noqa: BLE001  收尾失败不影响报告
        pass

    e2e = [(r.get("metrics") or {}).get("e2e_ms") or 0 for r in results_raw]
    first_answer = [
        (r.get("metrics") or {}).get("first_answer_ms") for r in results_raw
    ]
    first_answer = [v for v in first_answer if v is not None]
    total = len(results_raw)
    success = sum(1 for r in results_raw if r.get("status") == "completed")
    error = total - success
    availability = round(success / total, 4) if total else 0.0
    error_rate = round(error / total, 4) if total else 0.0
    latency = describe(e2e)

    payload = {
        "test_type": "stability",
        "fingerprint": settings.to_fingerprint_fields(),
        "args": {"concurrency": concurrency, "duration": duration_s},
        "business_count": total,
        "availability": availability,
        "error_rate": error_rate,
        "success_count": success,
        "error_count": error,
        "latency_ms": latency,
        "first_answer_ms": describe(first_answer),
    }
    out = stamp_and_write(
        settings, "stability", payload,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        data_path=data_path,
    )
    print(f"业务数={total} 时长={wall:.1f}s 可用性={availability} 错误率={error_rate} "
          f"E2E P90={latency['p90']}ms")
    print(f"结果已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())