"""Checkpoint 持久化专项：跨“进程重启”续跑验证（v2 入口）。

与 `checkpoint.py`（进程内内存存储）的区别：本脚本用 sqlite 存储，
在第 3 步**新建服务实例**（新的执行器与存储对象，等价于进程重启后再起），
从磁盘上的检查点续跑；对照组用 memory 存储验证新建实例找不到检查点。

用法：
    python -m tests.performance.checkpoint_persist
输出：reports/checkpoint_persist_workflow_<ts>.json
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.services.workflow_service import WorkflowService  # noqa: E402
from src.workflow.checkpoint_store import (  # noqa: E402
    MemoryCheckpointStore,
    SqliteCheckpointStore,
)
from harness.contracts import RunInput  # noqa: E402

QUERY = "我的订单 2024010100002 物流到哪里了？"
EXEC_ID = "cp-persist-001"


def make_service(store_kind: str, db_path: Path) -> WorkflowService:
    settings = Settings()
    settings.checkpoint_store = store_kind
    settings.checkpoint_db_path = db_path
    return WorkflowService(settings)


def inject_fault(service: WorkflowService, limit: int = 0) -> None:
    s = service.settings
    s.fault_enabled = True
    s.fault_node_id = "response_generation"
    s.fault_mode = "timeout"
    s.fault_latency_ms = 60
    s.fault_limit = limit
    s.max_retries = 0


def run(service: WorkflowService, query: str, execution_id: str | None = None) -> dict:
    return service.process(query, execution_id=execution_id)


def main() -> int:
    import asyncio

    workdir = Path(tempfile.mkdtemp(prefix="cp_persist_"))
    db_path = workdir / "checkpoints.db"
    checks: dict[str, dict] = {}

    # 1) 基线：无故障完整执行
    svc_a = make_service("sqlite", db_path)
    baseline = run(svc_a, QUERY)
    checks["S1_基线"] = {
        "通过": baseline["status"] == "completed" and len(baseline["path"]) == 4,
        "detail": f"status={baseline['status']} path={'→'.join(baseline['path'])}",
    }
    baseline_answer = baseline["response"]

    # 2) 故障注入：生成节点超时，检查点应已落盘（锚点=数据检索）
    inject_fault(svc_a)
    failed = run(svc_a, QUERY, execution_id=EXEC_ID)
    cp_a = svc_a.executor.checkpoints.load_latest(EXEC_ID)
    checks["S2_故障与存档"] = {
        "通过": failed["status"] == "failed" and cp_a is not None,
        "detail": (f"status={failed['status']} 锚点="
                   f"{cp_a.completed_node_id if cp_a else None} "
                   f"存储后端={svc_a.executor.checkpoints.backend}"),
    }

    # 3) 关键验证：新建服务实例（等价进程重启）从磁盘检查点续跑
    svc_b = make_service("sqlite", db_path)
    cp_b = svc_b.executor.checkpoints.load_latest(EXEC_ID)
    checks["S3_新实例读到检查点"] = {
        "通过": cp_b is not None and cp_b.completed_node_id == "data_retrieval",
        "detail": f"锚点={cp_b.completed_node_id if cp_b else None} 后端="
                  f"{svc_b.executor.checkpoints.backend}",
    }
    resumed = run(svc_b, QUERY, execution_id=EXEC_ID)
    resumed_path = resumed.get("path") or resumed.get("trace", {}).get("path") or []
    exec_recs = resumed.get("node_executions") or []
    # 幂等判据与内存版一致：意图识别/数据检索在恢复上下文中各只出现一次。
    # （检查点快照本身含首次执行的记录，续跑从 data_retrieval 之后继续，
    #   因此计数为 1 即证明未重复执行；若重跑会出现 2 次。）
    intent_count = sum(1 for r in exec_recs if r.get("node_id") == "intent_recognition")
    data_count = sum(1 for r in exec_recs if r.get("node_id") == "data_retrieval")
    checks["S4_续跑完成"] = {
        "通过": resumed["status"] == "completed",
        "detail": f"status={resumed['status']} 答复长度={len(resumed.get('response') or '')}",
    }
    checks["S5_答复与基线一致"] = {
        "通过": (resumed.get("response") or "") == baseline_answer,
        "detail": "续跑答复与 S1 基线逐字比对",
    }
    checks["S6_意图与检索未重跑"] = {
        "通过": intent_count == 1 and data_count == 1,
        "detail": f"intent_recognition={intent_count} 次 data_retrieval={data_count} 次"
                  f"（重跑会翻倍）",
    }

    # 4) 对照组：memory 存储下新建实例读不到检查点（诚实边界）
    svc_c = make_service("memory", workdir / "unused.db")
    inject_fault(svc_c)
    run(svc_c, QUERY, execution_id="cp-mem-001")
    svc_d = make_service("memory", workdir / "unused.db")
    checks["S7_内存存储对照组"] = {
        "通过": svc_d.executor.checkpoints.load_latest("cp-mem-001") is None,
        "detail": "新建实例在 memory 后端读不到检查点（进程重启即丢，符合预期）",
    }

    try:
        asyncio.run(svc_a.aclose())
        asyncio.run(svc_b.aclose())
        asyncio.run(svc_c.aclose())
        asyncio.run(svc_d.aclose())
    except Exception:  # noqa: BLE001  收尾失败不影响结论
        pass

    passed = sum(1 for c in checks.values() if c["通过"])
    checks["边界声明"] = {
        "detail": "本专项以新建服务实例等价进程重启，存储文件为真实 SQLite；"
                  "真实 kill -9 与容器级销毁重建恢复由 checkpoint_kill9.py / "
                  "checkpoint_container.py 承接（课程正文对应段落）"
    }

    payload = {
        "test_type": "checkpoint_persist",
        "fingerprint": Settings().to_fingerprint_fields(),
        "args": {"execution_id": EXEC_ID, "store": "sqlite", "db": str(db_path)},
        "checks": checks,
        "passed": f"{passed}/{len(checks) - 1}",
    }
    reports_dir = Settings().reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"checkpoint_persist_workflow_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, item in checks.items():
        if name == "边界声明":
            continue
        print(f"[{'通过' if item['通过'] else '失败'}] {name}: {item['detail']}")
    print(f"\n检查点持久化专项：{payload['passed']}")
    print(f"结果已写入：{out}")
    return 0 if passed == len(checks) - 1 else 1


if __name__ == "__main__":
    sys.exit(main())
