"""基准测试：全量 205 业务顺序执行（单用户基线），输出聚合报告。

产物：reports/benchmark_workflow_<ts>.json（含逐笔业务明细，供
分支覆盖 / 联合门禁复用，避免重复全量跑测）。

口径：四级判定 + 路径=预期 + 分支=预期 + 工具等效覆盖 + 意图一致。
"""
from __future__ import annotations

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
from tests.performance.common import aggregate, classify, load_tickets, stamp_and_write  # noqa: E402


async def run_all(settings, service, tickets, businesses, batch_id: str) -> None:
    from harness.contracts import RunInput

    for i, t in enumerate(tickets, 1):
        # v2：身份传递（task_id=工单号、batch_id=批次），经 Harness 运行入口执行
        result = await service.arun(RunInput(
            query=t.user_query, task_id=t.ticket_id, batch_id=batch_id,
        ))
        result = result.model_dump()
        businesses.append(classify(
            t, result, slo_ms=settings.active_slo()[0],
            first_answer_slo_ms=settings.active_slo()[1],
        ))
        if i % 50 == 0:
            print(f"进度 {i}/{len(tickets)}", flush=True)


def main() -> int:
    import asyncio

    settings = Settings()
    service = WorkflowService(settings)
    tickets = load_tickets(settings.data_dir / "tickets.json")
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    businesses = []
    batch_id = f"bench_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    started_at = datetime.now().isoformat(timespec="seconds")
    t0 = time.perf_counter()
    asyncio.run(run_all(settings, service, tickets, businesses, batch_id))
    duration_s = time.perf_counter() - t0
    try:
        asyncio.run(service.aclose())
    except Exception:  # noqa: BLE001
        pass

    agg = aggregate(businesses, duration_s, cost_per_1k=settings.token_price_per_1k)

    path_ok = sum(1 for b in businesses if b["path_ok"])
    branch_ok = sum(1 for b in businesses if b["branch_ok"])

    def _split(branch):
        return [b for b in businesses if (b["metrics"].get("branch") or "") == branch]

    latency_by_branch = {}
    for branch, subset in (("auto_reply", _split("auto_reply")),
                           ("clarification", _split("clarification")),
                           ("human_transfer", _split("human_transfer"))):
        if subset:
            latency_by_branch[branch] = aggregate(subset, duration_s,
                                                  cost_per_1k=settings.token_price_per_1k)

    from src.utils.metrics import describe

    e2e_list = [b["metrics"]["e2e_ms"] for b in businesses]
    payload = {
        "test_type": "benchmark",
        "batch_id": batch_id,
        "schema_version": 2,
        "run_id": f"benchmark-workflow-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "fingerprint": settings.to_fingerprint_fields(),
        "samples": len(tickets),
        "samples_main": sum(1 for b in businesses if b["metrics"]["path"] ==
                            ["intent_recognition", "data_retrieval",
                             "response_generation", "confidence_check"]),
        "samples_branch": sum(1 for b in businesses if
                              b["metrics"]["path"] == ["intent_recognition", "clarification"]),
        "duration_s": round(duration_s, 3),
        "path_match_rate": round(path_ok / len(tickets) * 100, 2),
        "branch_match_rate": round(branch_ok / len(tickets) * 100, 2),
        "e2e_summary": describe(e2e_list),
        "aggregate": agg,
        "latency_by_branch": latency_by_branch,
        "businesses": [{
            "ticket_id": b["ticket_id"], "qualified": b["levels"]["qualified"],
            "run_id": b.get("run_id", ""),
            "batch_id": b.get("batch_id", ""),
            "status": b.get("status"),
            "stop_reason": b.get("stop_reason", ""),
            "e2e_ms": b["metrics"]["e2e_ms"], "tokens": b["metrics"]["total_tokens"],
            "intent": b["metrics"]["trace"].get("intent"),
            "path": b["metrics"]["path"], "branch": b["metrics"]["branch"],
            "tools": b["metrics"]["tool_calls"],
            # 006 R04：逐笔要素命中明细，支撑 EvaluationRecord 与人工复核
            "quality": b.get("quality"),
        } for b in businesses],
    }

    out = stamp_and_write(
        settings, "benchmark", payload,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        data_path=settings.data_dir / "tickets.json",
    )

    print(json.dumps({
        "E2E_P90_ms": agg["时延指标(mm)"]["E2E"]["p90"],
        "合格率": agg["合格率"]["合格业务百分比"],
        "Goodput": agg["业务Goodput(合格业务/秒)"],
        "成本(元/合格业务)": agg["成本指标"]["成功业务成本(元/合格业务)"],
        "路径一致率": payload["path_match_rate"],
        "分支一致率": payload["branch_match_rate"],
        "时长_s": round(duration_s, 1),
    }, ensure_ascii=False))
    print(f"结果已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())