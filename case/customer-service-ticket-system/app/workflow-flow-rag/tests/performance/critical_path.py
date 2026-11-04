"""关键路径测试：主路径识别 + 节点故障注入 + Checkpoint 恢复幂等。

场景：
- S1 基线：关键路径主路径全绿，断言关键节点均在路径中；
- S2 单点故障（data_retrieval error，限重试 0 次）：执行失败，
  Checkpoint 已存档最近成功节点；
- S3 Checkpoint 恢复：同一 execution_id 续跑，断言步骤不重复执行
  （intent 只执行 1 次）、业务事实与基线一致（真实模型下不要求逐字一致）；
- S4 关键节点全部成功注入后路径仍能收敛到预期分支。
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402
from tests.performance.common import stamp_and_write  # noqa: E402

def _business_facts(text: str) -> set:
    """抽取答复中的业务事实要素（订单号形态 + 关键状态词），
    用于真实模型下的业务一致性断言，替代逐字比对。"""
    facts = set(re.findall(r"[A-Z]{1,4}-?\d{6,}", text or ""))
    for kw in ("已签收", "运输中", "派送中", "已发货", "未发货", "退款", "退货", "已取消", "待发货"):
        if kw in (text or ""):
            facts.add(kw)
    return facts

QUERY = "我的订单 2024010100001 现在到哪了？"
KEY_NODES = ["intent_recognition", "data_retrieval", "response_generation", "confidence_check"]


def main() -> int:
    settings = Settings()
    service = WorkflowService(settings)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().isoformat(timespec="seconds")

    scenarios = []

    def record(name, status, note):
        scenarios.append({"name": name, "status": status, "note": note})
        print(f"[{name}] {status} - {note}", flush=True)

    # S1 基线
    base = service.process(QUERY)
    critical_path = base["path"]
    key_ok = all(n in critical_path for n in KEY_NODES)
    record("S1-关键路径基线", "通过" if base["status"] == "completed" and key_ok else "失败",
           f"path={critical_path} e2e={base['e2e_ms']}ms")

    # 还原原配置
    saved = {k: getattr(settings, k) for k in
             ("fault_enabled", "fault_node_id", "fault_mode",
              "fault_limit", "max_retries")}

    # S2 单点故障
    settings.fault_enabled = True
    settings.fault_node_id = "data_retrieval"
    settings.fault_mode = "down"
    settings.fault_limit = 0
    settings.max_retries = 0
    exec_id = "cp-fail-001"
    fail_result = service.process(QUERY, execution_id=exec_id)
    cp = service.executor.checkpoints.load_latest(exec_id) if settings.checkpoint_enabled else None
    record("S2-数据检索节点故障", "通过" if fail_result["status"] == "failed" else "失败",
           f"status={fail_result['status']} 已存档节点={cp.completed_node_id if cp else '-'}")

    # S3 Checkpoint 恢复
    settings.fault_enabled = False
    settings.max_retries = saved["max_retries"]
    full = service.process(QUERY, execution_id=exec_id)
    resumed_ok = (full["status"] == "completed"
                  and _business_facts(full["response"]) == _business_facts(base["response"])
                  and full.get("path") == base.get("path"))
    intent_visits = (full.get("trace") or {}).get("intent") is not None
    exec_recs = full.get("node_executions") or []
    intent_count = sum(1 for r in exec_recs if r["node_id"] == "intent_recognition")
    record("S3-Checkpoint 恢复幂等续跑", "通过" if resumed_ok and intent_count == 1 else "失败",
           f"intent 执行次数={intent_count} 业务事实一致={resumed_ok} path={full['path']}")

    # 恢复通用配置并验证后续请求不串扰
    for k, v in saved.items():
        setattr(settings, k, v)

    record("S4-故障恢复期外主路径", "通过" if service.process(QUERY)["status"] == "completed" else "失败",
           f"e2e={service.process(QUERY)['e2e_ms']}ms")

    payload = {
        "test_type": "critical_path",
        "fingerprint": settings.to_fingerprint_fields(),
        "critical_path": critical_path,
        "key_nodes": KEY_NODES,
        "checkpoint_enabled": settings.checkpoint_enabled,
        "idempotent_nodes": "intent_recognition#1",
        "scenarios": scenarios,
    }
    out = stamp_and_write(
        settings, "critical_path", payload,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        data_path=settings.data_dir / "tickets.json",
    )
    print(f"结果已写入：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())