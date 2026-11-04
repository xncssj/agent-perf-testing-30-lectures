"""容量测试：阶梯并发加压（进程内并发多路业务），观测拐点与松耦合收益。

模型：每档并发提交 concurrency × duration_s 笔业务，经 v2 入口（arun）
并发执行；业务体在 to_thread 工作线程内承载 mock 阻塞延迟，事件循环
负责流出答复，因此本档 Goodput 反映真实的并发承载能力。
展示 Workflow 节点化后"LLM 调用瘦身 → 单业务时延下降 → 吞吐上升"的容量收益。
"""
from __future__ import annotations

import asyncio
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
from tests.performance.common import aggregate, classify, load_tickets  # noqa: E402
# harness 包位于案例根，需在 src 包导入（触发路径引导）之后引入
from harness.contracts import RunInput  # noqa: E402


async def run_all(settings, service, tickets, results) -> None:
    """全档位在同一事件循环内顺序执行。

    共享执行器的信号量在 Python 3.10+ 会绑定首个事件循环，逐档
    asyncio.run 新建循环会触发跨循环复用错误，因此统一在一个循环内跑完。
    """
    levels = [{"concurrency": 2, "duration_s": 20},
              {"concurrency": 6, "duration_s": 20},
              {"concurrency": 12, "duration_s": 20}]
    qs_tickets = tickets[: int(len(tickets) * 0.5)]

    async def one(t, idx: int, batch_id: str, gate: asyncio.Semaphore) -> tuple:
        async with gate:
            r = await service.arun(RunInput(
                query=t.user_query, task_id=f"{t.ticket_id}#{idx}",
                batch_id=batch_id,
            ))
            return t, r.model_dump()

    for lv in levels:
        concurrency = lv["concurrency"]
        total = concurrency * lv["duration_s"]
        batch_id = f"cap_{datetime.now().strftime('%Y%m%d%H%M%S')}_{concurrency}"
        # 档位并发上限：与原线程池 max_workers 语义一致，超出者排队等待
        gate = asyncio.Semaphore(concurrency)
        started = time.perf_counter()
        raw = await asyncio.gather(*(
            one(qs_tickets[i % len(qs_tickets)], i, batch_id, gate)
            for i in range(total)
        ))
        duration_s = time.perf_counter() - started
        pairs = raw
        # v2 入口携带首字实测，双指标 SLO（E2E + 首字）同时参与判定
        businesses = [
            classify(t, r, slo_ms=settings.active_slo()[0],
                     first_answer_slo_ms=settings.active_slo()[1])
            for t, r in pairs[:150]
        ]
        agg = aggregate(businesses, duration_s, cost_per_1k=settings.token_price_per_1k)
        results.append({
            "concurrency": concurrency,
            "duration_s": round(duration_s, 2),
            "时延指标(mm)": agg["时延指标(mm)"],
            "合格率": agg["合格率"],
            "业务Goodput(合格业务/秒)": agg["业务Goodput(合格业务/秒)"],
        })
        print(f"并发 {concurrency}: E2E_P90={agg['时延指标(mm)']['E2E']['p90']}ms "
              f"首字_P90={agg['时延指标(mm)']['首字'].get('p90')}ms "
              f"Goodput={agg['业务Goodput(合格业务/秒)']} 合格率="
              f"{agg['合格率']['合格业务百分比']}%", flush=True)


def main() -> int:
    settings = Settings()
    service = WorkflowService(settings)
    tickets = load_tickets(settings.data_dir / "tickets.json")
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    asyncio.run(run_all(settings, service, tickets, results))
    try:
        asyncio.run(service.aclose())
    except Exception:  # noqa: BLE001  收尾失败不影响报告
        pass

    payload = {
        "test_type": "capacity",
        "fingerprint": settings.to_fingerprint_fields(),
        "levels": results,
    }
    out = settings.reports_dir / f"capacity_workflow_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())