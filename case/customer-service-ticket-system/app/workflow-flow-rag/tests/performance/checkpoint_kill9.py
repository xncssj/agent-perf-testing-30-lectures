"""Checkpoint kill -9 专项：真实 SIGKILL 进程级恢复验证（v2 HTTP 入口）。

与 `checkpoint_persist.py` 的区别：不再以"新建服务实例"等价进程重启，
而是启动**真实 uvicorn 子进程**，在故障注入窗口内对其发送 SIGKILL，
随后重启进程并用同一 execution_id 续跑，验证：
1. kill -9 前检查点已真实落盘（锚点=data_retrieval）；
2. 进程确实被 SIGKILL 终止（wait 返回码 -9）；
3. 重启后同 execution_id 续跑完成、已完成节点不重复执行；
4. 续跑答复的业务事实与无故障基线一致（真实模型下不要求逐字一致）；
5. completed 终态后检查点按保留策略即时清理（生命周期，13.13）。

用法（阶段三目录，需老板授权执行——会真实杀掉本脚本启动的子进程）：
    python -m tests.performance.checkpoint_kill9
输出：reports/checkpoint_kill9_workflow_<ts>.json
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import Settings  # noqa: E402
from src.workflow.checkpoint_store import SqliteCheckpointStore  # noqa: E402

def _business_facts(text: str) -> set:
    """抽取答复中的业务事实要素（订单号形态 + 关键状态词），
    用于真实模型下的业务一致性断言，替代逐字比对。"""
    facts = set(re.findall(r"[A-Z]{1,4}-?\d{6,}", text or ""))
    for kw in ("已签收", "运输中", "派送中", "已发货", "未发货", "退款", "退货", "已取消", "待发货"):
        if kw in (text or ""):
            facts.add(kw)
    return facts

QUERY = "我的订单 2024010100002 物流到哪里了？"
EXEC_ID = "cp-kill9-001"
BASE_ID = "cp-kill9-baseline"

# 故障窗口：response_generation 注入 60s 超时且不重试——节点 1/2 完成存档后
# 进程必在其睡眠期内被杀（脚轮询到锚点即发 SIGKILL，远小于 60s）。
FAULT_LATENCY_MS = 60_000
HEALTH_TIMEOUT_S = 30.0
ANCHOR_TIMEOUT_S = 30.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_service(port: int, db_path: Path, log_path: Path, fault: bool,
                  logs: list) -> subprocess.Popen:
    """启动真实 uvicorn 子进程；fault=True 时注入 response_generation 长超时。"""
    env = dict(os.environ)
    env.update({
        "FLOW_CHECKPOINT_STORE": "sqlite",
        "FLOW_CHECKPOINT_DB": str(db_path),
        # 只绑定本机回环并关闭观测上报，实验进程不产生外部副作用
        "LANGFUSE_ENABLED": "false",
    })
    if fault:
        env.update({
            "FLOW_FAULT_ENABLED": "true",
            "FLOW_FAULT_NODE_ID": "response_generation",
            "FLOW_FAULT_MODE": "timeout",
            "FLOW_FAULT_LATENCY_MS": str(FAULT_LATENCY_MS),
            "FLOW_FAULT_LIMIT": "0",
            "FLOW_MAX_RETRIES": "0",
        })
    log = log_path.open("ab")
    logs.append(log)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=PROJECT_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )


def wait_health(port: int, timeout_s: float = HEALTH_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/health").status_code == 200:
                    return True
            except httpx.HTTPError:
                time.sleep(0.2)
    return False


def post_run(port: int, execution_id: str, timeout_s: float = 90.0) -> dict:
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=timeout_s) as client:
        r = client.post("/api/v1/chat/complete",
                        json={"query": QUERY, "execution_id": execution_id})
        r.raise_for_status()
        return r.json()


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="cp_kill9_"))
    db_path = workdir / "checkpoints.db"
    log_path = workdir / "service.log"
    checks: dict[str, dict] = {}
    procs: list[subprocess.Popen] = []
    logs: list = []

    def node_counts(result: dict) -> tuple[int, int]:
        recs = ((result.get("metrics") or {}).get("trace") or {}).get(
            "node_executions") or []
        return (
            sum(1 for r in recs if r.get("node_id") == "intent_recognition"),
            sum(1 for r in recs if r.get("node_id") == "data_retrieval"),
        )

    try:
        # 1) 基线：干净进程跑一遍，留答复基准（顺带验证真实 HTTP 链路与落盘配置）
        port_a = _free_port()
        proc_a = start_service(port_a, db_path, log_path, fault=False, logs=logs)
        procs.append(proc_a)
        checks["S1_基线服务"] = {
            "通过": wait_health(port_a),
            "detail": f"uvicorn 子进程 pid={proc_a.pid} 端口={port_a} "
                      f"sqlite={db_path.name}",
        }
        baseline = post_run(port_a, BASE_ID)
        checks["S2_基线运行"] = {
            "通过": baseline.get("status") == "completed",
            "detail": f"status={baseline.get('status')} "
                      f"stop_reason={baseline.get('stop_reason')}",
        }
        baseline_answer = baseline.get("response") or ""
        proc_a.terminate()
        proc_a.wait(timeout=15)

        # 2) 故障进程：等锚点落盘后 SIGKILL
        port_b = _free_port()
        proc_b = start_service(port_b, db_path, log_path, fault=True, logs=logs)
        procs.append(proc_b)
        if not wait_health(port_b):
            raise RuntimeError("故障进程未就绪")
        post_error: list[str] = []

        def fire_request() -> None:
            try:
                post_run(port_b, EXEC_ID)
            except Exception as exc:  # noqa: BLE001  预期被杀后连接中断
                post_error.append(f"{type(exc).__name__}")

        worker = threading.Thread(target=fire_request, daemon=True)
        worker.start()

        store = SqliteCheckpointStore(db_path)
        anchor = None
        deadline = time.monotonic() + ANCHOR_TIMEOUT_S
        while time.monotonic() < deadline:
            cp = store.load_latest(EXEC_ID)
            if cp is not None and cp.completed_node_id == "data_retrieval":
                anchor = cp
                break
            time.sleep(0.05)
        checks["S3_锚点已落盘"] = {
            "通过": anchor is not None,
            "detail": (f"anchor={anchor.completed_node_id if anchor else None} "
                       f"created_at={anchor.created_at if anchor else None} "
                       f"（此后 response_generation 进入 {FAULT_LATENCY_MS}ms 故障窗口）"),
        }

        os.kill(proc_b.pid, signal.SIGKILL)
        returncode = proc_b.wait(timeout=15)
        worker.join(timeout=5)
        checks["S4_SIGKILL送达"] = {
            "通过": returncode == -signal.SIGKILL and proc_b.poll() is not None,
            "detail": f"kill -9 pid={proc_b.pid} wait 返回码={returncode} "
                      f"（-9=SIGKILL）客户端侧={post_error or '请求未中断'}",
        }

        # 3) 重启进程：同 execution_id 续跑
        port_c = _free_port()
        proc_c = start_service(port_c, db_path, log_path, fault=False, logs=logs)
        procs.append(proc_c)
        if not wait_health(port_c):
            raise RuntimeError("重启进程未就绪")
        resumed = post_run(port_c, EXEC_ID)
        checks["S5_重启续跑完成"] = {
            "通过": resumed.get("status") == "completed",
            "detail": f"status={resumed.get('status')} "
                      f"stop_reason={resumed.get('stop_reason')}",
        }
        intent_n, data_n = node_counts(resumed)
        checks["S6_已完成节点未重跑"] = {
            "通过": (intent_n, data_n) == (1, 1),
            "detail": f"intent_recognition={intent_n} 次 data_retrieval={data_n} 次"
                      f"（重跑会翻倍）",
        }
        checks["S7_业务事实与基线一致"] = {
            "通过": _business_facts(resumed.get("response") or "") == _business_facts(baseline_answer),
            "detail": f"业务事实集比对（真实模型下不要求逐字一致）；"
                      f"续跑长度={len(resumed.get('response') or '')} 基线长度={len(baseline_answer)}",
        }
        # 4) 生命周期：续跑成功后检查点应被即时清理
        checks["S8_完成态检查点已清理"] = {
            "通过": SqliteCheckpointStore(db_path).load_latest(EXEC_ID) is None,
            "detail": "completed 终态后该 execution_id 检查点按保留策略删除"
                      f"（purge_on_completed={Settings().checkpoint_purge_on_completed}）",
        }
    except Exception as exc:  # noqa: BLE001  环境异常也必须留痕，不允许无报告失败
        checks["环境异常"] = {
            "通过": False,
            "detail": f"{type(exc).__name__}: {exc}（服务日志：{log_path}）",
        }
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        for log in logs:
            log.close()

    passed = sum(1 for c in checks.values() if c["通过"])
    payload = {
        "test_type": "checkpoint_kill9",
        "fingerprint": Settings().to_fingerprint_fields(),
        "args": {
            "execution_id": EXEC_ID, "baseline_id": BASE_ID,
            "store": "sqlite", "db": str(db_path),
            "signal": "SIGKILL", "fault_latency_ms": FAULT_LATENCY_MS,
            "service_log": str(log_path),
        },
        "checks": checks,
        "passed": f"{passed}/{len(checks)}",
    }
    reports_dir = Settings().reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"checkpoint_kill9_workflow_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, item in checks.items():
        print(f"[{'通过' if item['通过'] else '失败'}] {name}: {item['detail']}")
    print(f"\nkill -9 恢复专项：{payload['passed']}")
    print(f"结果已写入：{out}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
