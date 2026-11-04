"""故障自愈测试：注入有限次故障（limit=1）后服务自动恢复。

场景：response_generation 节点先失败 1 次，第二次调用恢复正常；
断言：首次执行失败被重试覆盖仍应成功（max_retries 兜底）或故障注入
self-heal 后成功——课程第 23 讲 Checkpoint/自愈应对。
"""
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

QUERY = "订单 2024010100003 什么时候能到？"


def main() -> int:
    settings = Settings()
    service = WorkflowService(settings)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)

    saved = {k: getattr(settings, k) for k in
             ("fault_enabled", "fault_node_id", "fault_mode", "fault_limit", "max_retries")}

    # 场景 A：timeout 注入 1 次后自愈（limit=1），依靠重试窗口
    settings.fault_enabled = True
    settings.fault_node_id = "response_generation"
    settings.fault_mode = "timeout"
    settings.fault_latency_ms = 60
    settings.fault_limit = 1
    settings.max_retries = 3
    baseline = service.process(QUERY)  # 未注入 execution_id
    healed = {
        "A_timeout_self_heal": baseline["status"] == "completed"
        and not (baseline.get("trace") or {}).get("node_exec_errors")
    }

    # 场景 B：error 注入 1 次（limit=1），第一次失败重试后成功
    settings.fault_mode = "error"
    service.executor.fault_failures = 0
    rb = service.process(QUERY)
    healed["B_error_self_heal"] = rb["status"] == "completed"
    healed["B_failures_counted"] = service.executor.fault_failures == 1

    # 场景 C：释放注入后完全正常
    settings.fault_enabled = False
    rc = service.process(QUERY)
    healed["C_clean"] = rc["status"] == "completed"

    for k, v in saved.items():
        setattr(settings, k, v)

    all_ok = all(healed.values())
    payload = {
        "test_type": "recovery",
        "fingerprint": settings.to_fingerprint_fields(),
        "scenario": {"mode": "timeout+error, limit=1, retry>=1"},
        "checks": healed,
        "all_ok": all_ok,
        "summary": "故障自愈达标" if all_ok else "自愈存在缺口",
    }
    out = settings.reports_dir / f"recovery_workflow_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(healed, ensure_ascii=False, indent=2))
    print(f"结果已写入：{out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())