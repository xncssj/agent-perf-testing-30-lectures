"""云端检查点续跑核验（B10 配套，方案 B5）。

背景：阶段三云端 recovery 此前只等健康检查恢复，`recovery.json` 写
`inflight_request_recovery: unverified`，不能证明"检查点续跑、已完成节点
不重跑"。本工具面向已恢复的云端服务，用固定 execution_id 发两笔请求
（检查点落盘前/后续跑），核对节点执行次数与业务事实一致性，输出核验 JSON，
供云端恢复脚本写入 recovery.json 的 checkpoint_resume 字段。

用法：
    python3 cloud_checkpoint_resume.py --base-url http://sut-agent.internal:8002 \\
        --exec-id cp-cloud-001 --query "我的订单 2024010100002 物流到哪里了？" \\
        [--field query] [--out resume.json]

判定：
- verified：状态 completed、已完成节点不重跑（intent/data 各 ≤1 次）、业务事实一致；
- mismatch：有数据但断言不成立（写明哪项）；
- unverified：接口不支持 execution_id 或服务不可达，写明原因。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

STATE_KEYWORDS = ("已签收", "运输中", "派送中", "已发货", "未发货", "退款", "退货", "已取消", "待发货")


def _business_facts(text: str) -> set:
    import re

    facts = set(re.findall(r"[A-Z]{1,4}-?\d{6,}", text or ""))
    for kw in STATE_KEYWORDS:
        if kw in (text or ""):
            facts.add(kw)
    return facts


def _node_records(payload: dict) -> list:
    """节点执行记录可能在顶层或 metrics.trace / trace 内（阶段三 RunResult 口径）。"""
    records = payload.get("node_executions")
    if records:
        return list(records)
    metrics = payload.get("metrics") or {}
    trace = metrics.get("trace") or payload.get("trace") or {}
    return list(trace.get("node_executions") or [])


def _node_counts(payload: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rec in _node_records(payload):
        node = rec.get("node_id")
        if node:
            counts[str(node)] = counts.get(str(node), 0) + 1
    return counts


def _request(client: httpx.Client, base_url: str, field: str, query: str,
             exec_id: str) -> dict:
    body = {field: query, "task_id": f"RESUME#{exec_id}", "execution_id": exec_id, "stream": False}
    resp = client.post("/api/v1/chat/complete", json=body)
    if resp.status_code == 400:
        return {"ok": False, "reason": f"HTTP 400（服务可能不支持 execution_id 字段）：{resp.text[:200]}"}
    resp.raise_for_status()
    return {"ok": True, "payload": resp.json()}


def check(base_url: str, exec_id: str, query: str, field: str, timeout_s: float) -> dict:
    with httpx.Client(base_url=base_url, timeout=timeout_s) as client:
        first = _request(client, base_url, field, query, exec_id)
        if not first.get("ok"):
            return {"status": "unverified", "execution_id": exec_id,
                    "reason": first.get("reason", "首笔请求失败")}
        second = _request(client, base_url, field, query, exec_id)
        if not second.get("ok"):
            return {"status": "unverified", "execution_id": exec_id,
                    "reason": second.get("reason", "续跑请求失败")}

    p1, p2 = first["payload"], second["payload"]
    counts2 = _node_counts(p2)
    no_replay = all(v <= 1 for v in counts2.values())
    facts_same = _business_facts(p1.get("response", "")) == _business_facts(p2.get("response", ""))
    problems = []
    if p2.get("status") != "completed":
        problems.append(f"续跑终态={p2.get('status')}")
    if not no_replay:
        problems.append(f"节点重跑：{counts2}")
    if not facts_same:
        problems.append("业务事实不一致")
    return {
        "status": "verified" if not problems else "mismatch",
        "execution_id": exec_id,
        "first_status": p1.get("status"),
        "resumed_status": p2.get("status"),
        "node_counts_resumed": counts2,
        "nodes_replayed": [k for k, v in counts2.items() if v > 1],
        "business_facts_first": sorted(_business_facts(p1.get("response", ""))),
        "business_facts_resumed": sorted(_business_facts(p2.get("response", ""))),
        "problems": problems,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="云端检查点续跑核验（B10）")
    parser.add_argument("--base-url", required=True, help="已恢复的云端服务地址")
    parser.add_argument("--exec-id", required=True, help="固定 execution_id")
    parser.add_argument("--query", required=True, help="用于触发执行链的业务问题")
    parser.add_argument("--field", choices=("query", "user_query"), default="query",
                        help="请求字段名：阶段三用 query，阶段一/二用 user_query")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", help="输出 JSON 路径；缺省打印到 stdout")
    args = parser.parse_args(argv)

    try:
        result = check(args.base_url, args.exec_id, args.query, args.field, args.timeout)
    except httpx.HTTPError as exc:
        result = {"status": "unverified", "execution_id": args.exec_id,
                  "reason": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"已写出：{out}")
    else:
        print(text)
    return 0 if result.get("status") == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
