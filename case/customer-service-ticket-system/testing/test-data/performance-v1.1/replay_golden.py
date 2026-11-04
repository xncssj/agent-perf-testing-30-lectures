"""performance-v1.1 黄金用例逐笔回放（服务级质量门禁证据）。

007 数据档战役整改 R03：对生成档的 golden-stage{1,2,3}.json 逐笔发起真实
HTTP 请求回放，按各阶段既有业务判定核对，产出逐笔结果与分类汇总：
- 预期不可达 / 工具缺口 / 事实缺口 / 关键词缺口 / 路径或分支缺口分列；
- 阶段一、二从实际执行轨迹（metrics.trace.tool_calls）核对 expected_tools，
  阶段二另核对必需 Agent 与校验 verdict；缺轨迹或缺 verdict 记 unverified；
- 请求层失败与系统终态失败单列，不计入业务合格；
- 故障窗口专用预期（stage2 注入用例）单独归类，不并入正常池通过率。

用法（案例根目录）：
    python3 testing/test-data/performance-v1.1/replay_golden.py \
        --data-dir testing/test-data/performance-v1.1/out/reference-balanced-uniform
默认自起三阶段服务（MOCK_RULE_VERSION=performance-v1.1，mock provider，
临时 SQLite 空库），回放结束自动停止；--server-1/2/3 可指向已运行实例。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASE_ROOT = HERE.parents[2]
APP_ROOT = CASE_ROOT / "app"

STAGE_DIRS = {
    1: APP_ROOT / "single-agent",
    2: APP_ROOT / "multi-agent",
    3: APP_ROOT / "workflow-flow-rag",
}
STAGE_PORTS = {1: 8000, 2: 8001, 3: 8002}
MOCK_RULE_VERSION = "performance-v1.1"

# 各阶段回放报告在 main 内填充（stage -> report）
reports: dict[int, dict] = {}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _http_json(method: str, url: str, payload: dict | None = None,
               timeout: float = 120.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def start_service(stage: int, port: int, mock_rule: str) -> tuple[subprocess.Popen, dict]:
    """以 v1.1 Mock 规则启动阶段服务（临时空 SQLite 库，避免复用旧知识库）。"""
    stage_dir = STAGE_DIRS[stage]
    python = stage_dir / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)
    tmpdir = tempfile.mkdtemp(prefix=f"replay-stage{stage}-")
    env = dict(os.environ)
    env.update({
        "LLM_PROVIDER": "mock",
        "MOCK_RULE_VERSION": mock_rule,
        "MOCK_PLAN_LATENCY_MS": "1", "MOCK_PLAN_JITTER_MS": "1",
        "MOCK_TOKEN_LATENCY_MS": "1",
    })
    if stage in (1, 2):
        env["AGENT_DB_PATH"] = str(Path(tmpdir) / "agent.db")
    if stage in (2, 3):
        # 仓库内 harness 包位于 app/（容器内由 Dockerfile 复制到阶段目录）
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [
            str(APP_ROOT), env.get("PYTHONPATH", "")]))
    if stage == 3:
        env.update({"MOCK_INTENT_LATENCY_MS": "1", "MOCK_INTENT_JITTER_MS": "1",
                    "MOCK_GENERATION_LATENCY_MS": "1", "MOCK_GENERATION_JITTER_MS": "1",
                    "MOCK_CLARIFICATION_LATENCY_MS": "1", "MOCK_CLARIFICATION_JITTER_MS": "1"})
    proc = subprocess.Popen(
        [str(python), "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=stage_dir, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, {"stage": stage, "port": port, "pid": proc.pid, "tmpdir": tmpdir}


def wait_version(port: int, mock_rule: str, timeout_s: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _, payload = _http_json("GET", f"http://127.0.0.1:{port}/version", timeout=5)
            actual = payload.get("mock_rule_version")
            if actual != mock_rule:
                raise SystemExit(
                    f"/version 的 mock_rule_version={actual!r}，与 {mock_rule!r} 不符；"
                    "拒绝回放（规则版本不可识别）")
            return payload
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_error = str(exc)
            time.sleep(1.0)
    raise SystemExit(f"服务 /version 未就绪：{last_error}")


def _tool_trace(data: dict) -> tuple[list[str], bool]:
    """从实际执行轨迹提取已调用的工具名；返回（工具名列表, 轨迹是否存在）。

    阶段一在 metrics.trace.tool_calls（AgentResult），阶段二在顶层 trace
    （与 metrics.trace 同结构）；缺轨迹按未验证处理，不视作通过。
    """
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    trace = metrics.get("trace") or data.get("trace") or {}
    if not isinstance(trace, dict) or "tool_calls" not in trace:
        return [], False
    calls = trace.get("tool_calls") or []
    if not isinstance(calls, list):
        return [], False
    return [c.get("tool") for c in calls if isinstance(c, dict)], True


def judge_stage1(case: dict, data: dict, check_tools: bool = True) -> dict:
    gaps = {"status": data.get("status"), "fact_miss": [], "keyword_miss": []}
    response = data.get("response", "")
    gaps["keyword_miss"] = [k for k in (case.get("expected_answer_keywords") or [])
                            if k and k not in response]
    gaps["fact_miss"] = [f"{k}={v}" for k, v in (case.get("expected_facts") or {}).items()
                         if v is not None and str(v) != "" and str(v) not in response]
    ok = data.get("status") == "completed" and not gaps["keyword_miss"] and not gaps["fact_miss"]
    # 工具轨迹核对（007 整改）：expected_tools 必须出现在实际执行轨迹中。
    # 阶段三的知识路径由检索节点等价承载，工具核对在其判定器内单独处理。
    if check_tools:
        tools, trace_present = _tool_trace(data)
        if trace_present:
            gaps["tools_seen"] = tools
            gaps["tool_miss"] = [t for t in (case.get("expected_tools") or [])
                                 if t not in tools]
            if gaps["tool_miss"]:
                ok = False
        else:
            gaps["trace_missing"] = True
            ok = False
    return {"passed": ok, **gaps}


def judge_stage2(case: dict, data: dict) -> dict:
    result = judge_stage1(case, data)
    # Agent 路径核对（与 tests/performance/common.py 同口径：幽灵期望跳过）
    trace = (data.get("metrics") or {}).get("trace") or data.get("trace") or {}
    if not isinstance(trace, dict) or not trace:
        return result
    plan = set(trace.get("plan_agents") or [])
    expected_tools = set(case.get("expected_tools") or [])
    expected_agents = list(case.get("expected_agents") or [])
    tool_for_agent_map = {"order_agent": "query_order", "logistics_agent": "query_logistics",
                          "knowledge_agent": "search_knowledge"}
    missing_agents = []
    for expected in expected_agents:
        tool_for_agent = tool_for_agent_map.get(expected)
        if tool_for_agent and tool_for_agent in expected_tools and expected not in plan:
            missing_agents.append(expected)
        # 校验 Agent 无对应工具映射：声明即必须出现在实际编排轨迹中
        if expected == "validation_agent" and expected not in plan:
            missing_agents.append(expected)
    if missing_agents:
        result["passed"] = False
        result["agent_miss"] = missing_agents
    # 校验状态核对（007 整改）：声明 validation_agent 的用例必须产出 verdict；
    # verdict 取值如实记录，incomplete 对 pending/知识类用例属合法形态，
    # 不预设业务结论、不因取值判失败，仅缺 verdict 按未验证处理
    if "validation_agent" in expected_agents:
        validation = trace.get("validation")
        if isinstance(validation, dict) and validation.get("status"):
            result["validation_status"] = validation.get("status")
        else:
            result["passed"] = False
            result["validation_missing"] = True
    return result


def judge_stage3(case: dict, data: dict) -> dict:
    metrics = data.get("metrics") or {}
    # HTTP 形态下路径/分支/工具调用记录在 metrics.trace（进程内执行器才在顶层）
    trace = metrics.get("trace") or {}
    response = data.get("response", "")
    result = judge_stage1(case, data, check_tools=False)
    path = trace.get("path") or metrics.get("path") or []
    branch = trace.get("branch") or metrics.get("branch") or ""
    tools = {call.get("tool") for call in (trace.get("tool_calls")
                                           or metrics.get("tool_calls") or [])}
    expected_tools = list(case.get("expected_tools") or [])
    knowledge_intents = {"return_policy", "exchange_policy", "contact_info"}
    intent = data.get("intent") or ""
    # search_knowledge 由 knowledge_retrieval 节点等价承载（与判定模块同口径）
    tool_miss = [t for t in expected_tools
                 if t not in tools
                 and not (t == "search_knowledge" and intent in knowledge_intents)]
    path_miss = path != (case.get("expected_path") or []) if case.get("expected_path") else False
    branch_miss = (branch != case["expected_branch"]) if case.get("expected_branch") else False
    if not isinstance(trace, dict) or "tool_calls" not in trace:
        # 缺执行轨迹按未验证处理（007 整改口径与阶段一/二一致）
        result["passed"] = False
        result["trace_missing"] = True
    if tool_miss:
        result["tool_miss"] = tool_miss
    if path_miss:
        result["path_actual"] = path
    if branch_miss:
        result["branch_actual"] = branch
    if tool_miss or path_miss or branch_miss:
        result["passed"] = False
    return result


JUDGES = {1: judge_stage1, 2: judge_stage2, 3: judge_stage3}


def replay_stage(stage: int, cases: list[dict], port: int, batch_id: str) -> dict:
    judge = JUDGES[stage]
    results = []
    counts: dict[str, int] = {}
    for index, case in enumerate(cases, 1):
        fault_profile = (case.get("perf_tags") or {}).get("fault_profile")
        entry = {"case_id": case["case_id"], "family": case.get("family", ""),
                 "fault_profile": fault_profile or "none"}
        try:
            if stage == 3:
                payload = {"query": case["user_query"], "task_id": case["ticket_id"],
                           "batch_id": batch_id}
            else:
                payload = {"user_query": case["user_query"], "task_id": case["ticket_id"],
                           "batch_id": batch_id, "stream": False}
            # 三阶段路由统一挂在 /api/v1 前缀下（阶段三 router prefix=/api/v1）
            _, data = _http_json("POST",
                                 f"http://127.0.0.1:{port}/api/v1/chat/complete", payload)
        except (urllib.error.URLError, ConnectionError, OSError, json.JSONDecodeError) as exc:
            entry.update({"category": "request_failed", "error": str(exc)[:300]})
            results.append(entry)
            counts["request_failed"] = counts.get("request_failed", 0) + 1
            print(f"[stage{stage} {index:02d}/{len(cases)}] {case['case_id']} 请求失败: {exc}")
            continue
        checked = judge(case, data)
        if fault_profile and fault_profile != "none":
            category = "fault_window_deferred"
        elif checked.pop("passed"):
            category = "pass"
        elif checked.get("trace_missing") or checked.get("validation_missing"):
            # 缺执行轨迹或缺校验 verdict：按未验证记录，不并入业务合格（007 整改）
            category = "unverified"
        else:
            category = "business_fail"
        entry.update({"category": category, "status": checked.pop("status", None),
                      **{k: v for k, v in checked.items() if v}})
        results.append(entry)
        counts[category] = counts.get(category, 0) + 1
        flag = "OK " if category in ("pass", "fault_window_deferred") else "MISS"
        print(f"[stage{stage} {index:02d}/{len(cases)}] {flag} {case['case_id']} "
              f"({entry.get('family')}) {category}")
    normal_total = sum(v for k, v in counts.items() if k != "fault_window_deferred")
    return {"stage": stage, "golden_count": len(cases),
            "normal_total": normal_total,
            "passed": counts.get("pass", 0),
            "category_counts": dict(sorted(counts.items())),
            "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=HERE / "out" / "reference-balanced-uniform")
    parser.add_argument("--stage", choices=("all", "1", "2", "3"), default="all")
    parser.add_argument("--port-base", type=int, default=18100)
    parser.add_argument("--mock-rule", default=MOCK_RULE_VERSION)
    parser.add_argument("--server-1", default="")
    parser.add_argument("--server-2", default="")
    parser.add_argument("--server-3", default="")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    stages = (1, 2, 3) if args.stage == "all" else (int(args.stage),)
    servers = {1: args.server_1, 2: args.server_2, 3: args.server_3}

    procs = []
    meta = []
    batch_id = f"replay-v11-{time.strftime('%Y%m%dT%H%M%S')}"
    try:
        for stage in stages:
            port = args.port_base + stage
            if servers[stage]:
                host = servers[stage].replace("http://", "").split("/")[0]
                port = int(host.split(":")[1]) if ":" in host else 80
            proc = None
            if not servers[stage]:
                proc, info = start_service(stage, port, args.mock_rule)
                procs.append(proc)
                meta.append(info)
                print(f"stage{stage} 服务已启动 pid={proc.pid} port={port}")
            version = wait_version(port, args.mock_rule)
            print(f"stage{stage} /version 就绪 mock_rule_version={version.get('mock_rule_version')} "
                  f"fingerprint={version.get('fingerprint')}")
            cases = _load(args.data_dir / f"golden-stage{stage}.json")
            report = replay_stage(stage, cases, port, batch_id)
            reports.setdefault(stage, report)
    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    payload = {
        "schema_version": "golden-replay-v1.1",
        # 判定口径（007 整改）：关键词/事实/expected_tools 实际执行轨迹核对
        #（阶段一/二）+ 必需 Agent 与校验 verdict（阶段二）+ 路径分支（阶段三）；
        # 缺轨迹或缺 verdict 记 unverified，不并入业务合格
        "judge_profile": "keywords+facts+tools_trace+agents+validation_status+path_branch",
        "batch_id": batch_id,
        "data_dir": str(args.data_dir),
        "mock_rule_version": args.mock_rule,
        "dataset_manifest_sha256": _sha(args.data_dir / "manifest.json"),
        "stages": {str(k): v for k, v in reports.items()},
    }
    out = args.out or (args.data_dir / "golden-replay-report.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({str(k): {"normal_total": v["normal_total"], "passed": v["passed"],
                               "category_counts": v["category_counts"]}
                      for k, v in reports.items()}, ensure_ascii=False, indent=2))
    print(f"回放报告：{out}")
    failed = any(v["category_counts"].get("business_fail", 0)
                 or v["category_counts"].get("request_failed", 0)
                 or v["category_counts"].get("unverified", 0)
                 for v in reports.values())
    raise SystemExit(1 if failed else 0)


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    main()
