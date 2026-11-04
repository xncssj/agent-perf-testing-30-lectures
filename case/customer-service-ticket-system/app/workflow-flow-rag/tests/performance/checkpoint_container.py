"""Checkpoint 容器级恢复专项：真实容器 SIGKILL + 销毁重建后续跑（13.14）。

与 `checkpoint_kill9.py`（宿主机进程 SIGKILL）的区别：被杀对象是
docker compose 编排的**真实容器**（cs-agent-python-workflow）：
1. FLOW_CHECKPOINT_STORE=sqlite + 命名卷 cs_agent_flow_checkpoints 落盘；
2. 故障注入窗口内 `docker kill -s KILL`（容器内 1 号进程收到 SIGKILL，
   退出码 137=128+9，非 OOM）；
3. **销毁重建**（--force-recreate，新容器、新文件系统层，仅命名卷保留），
   同 execution_id 续跑完成、已完成节点不重跑、答复与基线逐字一致；
4. completed 终态后检查点按保留策略即时清理。

用法（需老板授权执行——会重建/杀掉本机 cs-agent-python-workflow 容器）：
    python -m tests.performance.checkpoint_container
输出：reports/checkpoint_container_workflow_<ts>.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
CASE_ROOT = PROJECT_ROOT.parent

from src.config.settings import Settings  # noqa: E402

QUERY = "我的订单 2024010100002 物流到哪里了？"
EXEC_ID = "cp-ctr-001"
BASE_ID = "cp-ctr-baseline"
CONTAINER = "cs-agent-python-workflow"
BASE_URL = "http://127.0.0.1:8002"
FAULT_LATENCY_MS = 60_000
VOLUME = "cs-agent_cs_agent_flow_checkpoints"

# 容器内读取检查点锚点的程序（subprocess 直接经 argv 传入，无 shell 引号
# 问题；__EID__ 占位符替换，避免 format 与 JSON 花括号冲突）
_READ_CP_TEMPLATE = """import json, os, sqlite3
p = os.environ.get('FLOW_CHECKPOINT_DB', '/app/data/checkpoints.db')
r = None
e = ''
try:
    c = sqlite3.connect(p)
    r = c.execute('select completed_node_id, created_at from checkpoints '
                  'where execution_id = ?', (__EID__,)).fetchone()
except Exception as x:
    e = str(x)
print(json.dumps({'anchor': r[0] if r else None,
                  'created_at': r[1] if r else None, 'error': e}))
"""


def compose(*args: str, env_extra: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("LANGFUSE_ENABLED", "false")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["docker", "compose", "--profile", "workflow", *args],
        cwd=CASE_ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


def docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout,
    )


def up_recreate(env_extra: dict) -> None:
    """销毁重建 agent-workflow 容器（不动依赖服务）。"""
    r = compose("up", "-d", "--no-deps", "--force-recreate", "agent-workflow",
                env_extra=env_extra)
    if r.returncode != 0:
        raise RuntimeError(f"compose up 失败: {r.stderr.strip()[:400]}")


def wait_health(timeout_s: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_s
    with httpx.Client(base_url=BASE_URL, timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                if client.get("/health").status_code == 200:
                    return True
            except httpx.HTTPError:
                time.sleep(0.5)
    return False


def post_run(execution_id: str, timeout_s: float = 90.0) -> dict:
    with httpx.Client(base_url=BASE_URL, timeout=timeout_s) as client:
        r = client.post("/api/v1/chat/complete",
                        json={"query": QUERY, "execution_id": execution_id})
        r.raise_for_status()
        return r.json()


def read_anchor(execution_id: str) -> dict:
    code = _READ_CP_TEMPLATE.replace("__EID__", repr(execution_id))
    r = docker("exec", CONTAINER, "python", "-c", code)
    if r.returncode != 0:
        return {"anchor": None, "error": r.stderr.strip()[:200]}
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"anchor": None, "error": f"输出不可解析: {r.stdout[:200]}"}


def container_state() -> str:
    r = docker("inspect", "-f",
               "{{.State.Status}}:{{.State.ExitCode}}:{{.State.OOMKilled}}:{{.Id}}",
               CONTAINER)
    return r.stdout.strip() if r.returncode == 0 else f"inspect 失败: {r.stderr.strip()}"


def base_env() -> dict:
    return {"FLOW_CHECKPOINT_STORE": "sqlite"}


def fault_env() -> dict:
    return {
        **base_env(),
        "FLOW_FAULT_ENABLED": "true",
        "FLOW_FAULT_NODE_ID": "response_generation",
        "FLOW_FAULT_MODE": "timeout",
        "FLOW_FAULT_LATENCY_MS": str(FAULT_LATENCY_MS),
        "FLOW_FAULT_LIMIT": "0",
        "FLOW_MAX_RETRIES": "0",
    }


def node_counts(result: dict) -> tuple[int, int]:
    recs = ((result.get("metrics") or {}).get("trace") or {}).get(
        "node_executions") or []
    return (
        sum(1 for r in recs if r.get("node_id") == "intent_recognition"),
        sum(1 for r in recs if r.get("node_id") == "data_retrieval"),
    )


def main() -> int:
    checks: dict[str, dict] = {}
    image_id = ""
    try:
        # 0) 清理既有同名容器（可能存在历史退出容器），重建镜像确保含当前代码
        compose("rm", "-sf", "agent-workflow", timeout=60)
        build = compose("build", "agent-workflow", timeout=600)
        checks["S1_镜像构建"] = {
            "通过": build.returncode == 0,
            "detail": f"docker compose build agent-workflow "
                      f"({'成功' if build.returncode == 0 else build.stderr.strip()[:200]})",
        }
        img = docker("images", "-q", "cs-agent-python-workflow:1.0.0")
        image_id = img.stdout.strip()[:12]

        # 1) 基线容器：sqlite + 命名卷，无故障
        up_recreate(base_env())
        checks["S2_基线容器"] = {
            "通过": wait_health(),
            "detail": f"容器={container_state()} 卷={VOLUME} 镜像={image_id}",
        }
        baseline = post_run(BASE_ID)
        checks["S3_基线运行"] = {
            "通过": baseline.get("status") == "completed",
            "detail": f"status={baseline.get('status')} "
                      f"stop_reason={baseline.get('stop_reason')}",
        }
        baseline_answer = baseline.get("response") or ""
        baseline_container = container_state().split(":")[-1][:12]

        # 2) 故障容器：锚点落盘后 docker kill -s KILL
        up_recreate(fault_env())
        if not wait_health():
            raise RuntimeError("故障容器未就绪")
        fault_container = container_state().split(":")[-1][:12]
        post_error: list[str] = []

        def fire_request() -> None:
            try:
                post_run(EXEC_ID)
            except Exception as exc:  # noqa: BLE001  预期被杀后连接中断
                post_error.append(f"{type(exc).__name__}")

        worker = threading.Thread(target=fire_request, daemon=True)
        worker.start()

        anchor_info: dict = {"anchor": None}
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            anchor_info = read_anchor(EXEC_ID)
            if anchor_info.get("anchor") == "data_retrieval":
                break
            time.sleep(0.5)
        checks["S4_锚点已落盘"] = {
            "通过": anchor_info.get("anchor") == "data_retrieval",
            "detail": f"容器内卷读取 {json.dumps(anchor_info, ensure_ascii=False)}"
                      f"（此后 response_generation 进入 {FAULT_LATENCY_MS}ms 故障窗口）",
        }

        kill = docker("kill", "-s", "KILL", CONTAINER)
        time.sleep(1.0)
        state = container_state()
        # 结构化解析 status:exit:oom:id；137=128+9 即 SIGKILL 终止，且非 OOM
        parts = state.split(":")
        killed_ok = (
            kill.returncode == 0
            and len(parts) >= 3
            and parts[0] == "exited"
            and parts[1] == "137"
            and parts[2].lower() == "false"
        )
        worker.join(timeout=5)
        checks["S5_容器SIGKILL"] = {
            "通过": killed_ok,
            "detail": f"docker kill -s KILL → 状态={parts[0] if parts else '?'} "
                      f"退出码={parts[1] if len(parts) > 1 else '?'} "
                      f"OOM={parts[2] if len(parts) > 2 else '?'} "
                      f"客户端侧={post_error or '请求未中断'}（137=128+9，SIGKILL）",
        }

        # 3) 销毁重建容器（新容器 ID，仅命名卷保留）：同 execution_id 续跑
        up_recreate(base_env())
        if not wait_health():
            raise RuntimeError("重建容器未就绪")
        resumed_container = container_state().split(":")[-1][:12]
        checks["S6_容器销毁重建"] = {
            "通过": resumed_container not in ("", baseline_container, fault_container),
            "detail": f"基线容器={baseline_container} 故障容器={fault_container} "
                      f"重建容器={resumed_container}（应为全新容器，卷 {VOLUME} 保留）",
        }
        resumed = post_run(EXEC_ID)
        checks["S7_重建后续跑完成"] = {
            "通过": resumed.get("status") == "completed",
            "detail": f"status={resumed.get('status')} "
                      f"stop_reason={resumed.get('stop_reason')}",
        }
        intent_n, data_n = node_counts(resumed)
        checks["S8_已完成节点未重跑"] = {
            "通过": (intent_n, data_n) == (1, 1),
            "detail": f"intent_recognition={intent_n} 次 data_retrieval={data_n} 次"
                      f"（重跑会翻倍）",
        }
        checks["S9_答复与基线一致"] = {
            "通过": (resumed.get("response") or "") == baseline_answer,
            "detail": f"续跑答复长度={len(resumed.get('response') or '')} "
                      f"基线长度={len(baseline_answer)} 逐字比对",
        }
        checks["S10_完成态检查点已清理"] = {
            "通过": read_anchor(EXEC_ID).get("anchor") is None,
            "detail": "completed 终态后该 execution_id 检查点按保留策略删除"
                      f"（容器内卷读取 {json.dumps(read_anchor(EXEC_ID), ensure_ascii=False)}）",
        }
    except Exception as exc:  # noqa: BLE001  环境异常也必须留痕，不允许无报告失败
        checks["环境异常"] = {
            "通过": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    finally:
        # 清理实验容器与专用卷，恢复实验前状态（该容器原本处于退出态）
        compose("rm", "-sf", "agent-workflow", timeout=60)
        docker("volume", "rm", VOLUME, timeout=30)

    passed = sum(1 for c in checks.values() if c["通过"])
    payload = {
        "test_type": "checkpoint_container",
        "fingerprint": Settings().to_fingerprint_fields(),
        "args": {
            "execution_id": EXEC_ID, "baseline_id": BASE_ID,
            "container": CONTAINER, "image": image_id,
            "volume": VOLUME, "signal": "SIGKILL(docker kill -s KILL)",
            "fault_latency_ms": FAULT_LATENCY_MS,
            "store": "sqlite", "recreate": "--force-recreate（新容器，卷保留）",
        },
        "checks": checks,
        "passed": f"{passed}/{len(checks)}",
    }
    reports_dir = Settings().reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"checkpoint_container_workflow_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, item in checks.items():
        print(f"[{'通过' if item['通过'] else '失败'}] {name}: {item['detail']}")
    print(f"\n容器级恢复专项：{payload['passed']}")
    print(f"结果已写入：{out}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
