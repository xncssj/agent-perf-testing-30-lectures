"""Checkpoint 专项：执行存档、失败后恢复、恢复上下文一致性。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402

QUERY = "我的订单 2024010100002 物流到哪里了？"


def main() -> int:
    settings = Settings()
    service = WorkflowService(settings)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    saved = {k: getattr(settings, k) for k in
             ("fault_enabled", "fault_node_id", "fault_mode", "fault_limit", "max_retries")}
    checks = {}

    # 无故障完整执行，得到基线
    baseline = service.process(QUERY)
    checks["baseline"] = baseline["status"] == "completed" and len(baseline["path"]) == 4

    # 注入 response_generation 故障（关键节点），限重试 0 次
    settings.fault_enabled = True
    settings.fault_node_id = "response_generation"
    settings.fault_mode = "timeout"
    settings.fault_latency_ms = 60
    settings.fault_limit = 0
    settings.max_retries = 0
    exec_id = "cp-assert-001"
    failed = service.process(QUERY, execution_id=exec_id)
    cp = service.executor.checkpoints.load_latest(exec_id)
    checks["failed_as_expected"] = failed["status"] == "failed" and cp is not None
    checks["checkpoint_saved"] = (cp is not None and
                                  cp.completed_node_id == "data_retrieval")

    # 恢复后续跑，允许从 data_retrieval 之后继续
    settings.fault_enabled = False
    settings.max_retries = saved["max_retries"]
    resumed = service.process(QUERY, execution_id=exec_id)
    exec_recs = resumed.get("node_executions") or []
    intent_count = sum(1 for r in exec_recs if r["node_id"] == "intent_recognition")
    data_count = sum(1 for r in exec_recs if r["node_id"] == "data_retrieval")
    checks["resume_completed"] = resumed["status"] == "completed"
    checks["resume_response_match"] = resumed["response"] == baseline["response"]
    checks["idempotent_intent"] = intent_count == 1
    checks["idempotent_data"] = data_count == 1

    for k, v in saved.items():
        setattr(settings, k, v)

    all_ok = all(checks.values())
    payload = {
        "test_type": "checkpoint",
        "fingerprint": settings.to_fingerprint_fields(),
        "query": QUERY,
        "checks": checks,
        "all_ok": all_ok,
        "summary": "Checkpoint 幂等续跑达标" if all_ok else "存在未达标项",
    }
    out = settings.reports_dir / f"checkpoint_workflow_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print(f"结果已写入：{out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())