"""共享工具池配对采集（方案 P2-18）。

背景：`AGENT_PARALLEL_LIMIT` 只限制单笔业务扇出宽度（P2-15 扇出宽度对照），
共享工具池由 agent_config.yaml 的 `limits.tool_workers` / `limits.tool_queue_size`
控制，且无环境变量覆盖。本工具不自动改配置，避免误伤运行中的服务；它负责：

1. `--show-config`：只读打印当前配置里的工具池参数（配对前核对）；
2. `--record <label>`：按给定并发跑一轮小样本，逐笔记录业务结果并采样
   `/metrics` 的 harness_tool_calls_inflight / harness_tool_overload_total /
   agent_runs_inflight，输出 JSON；
3. `--compare <a.json> <b.json>`：对比两轮读数（默认配置 vs 低容量配置）。

低容量配置由人工修改 agent_config.yaml 并重部署后执行第二轮；两轮须同
数据、同并发、同 SLO，比较差值才有意义。

用法：
    python3 tool_pool_pair.py --show-config
    python3 tool_pool_pair.py --record default --base-url http://127.0.0.1:8001 --samples 30 --concurrency 5
    python3 tool_pool_pair.py --record low --base-url http://127.0.0.1:8001 --samples 30 --concurrency 5
    python3 tool_pool_pair.py --compare pair_default_*.json pair_low_*.json
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "src" / "config" / "agent_config.yaml"
METRIC_NAMES = ("harness_tool_calls_inflight", "harness_tool_overload_total", "agent_runs_inflight")


def _show_config() -> dict:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    found = {}
    for key in ("tool_workers", "tool_queue_size"):
        match = re.search(rf"^\s*{key}\s*:\s*(\d+)\s*$", text, re.MULTILINE)
        found[key] = int(match.group(1)) if match else None
    print(json.dumps({"config": str(CONFIG_PATH), "limits": found}, ensure_ascii=False, indent=2))
    return found


def _metric_values(client: httpx.Client) -> dict:
    try:
        text = client.get("/metrics", timeout=5).text
    except httpx.HTTPError:
        return {name: None for name in METRIC_NAMES}
    values = {}
    for name in METRIC_NAMES:
        value = None
        for line in text.splitlines():
            if line.startswith(name + " ") or line.startswith(name + "{"):
                try:
                    value = float(line.rsplit(" ", 1)[-1])
                except ValueError:
                    value = None
                break
        values[name] = value
    return values


def _send(client: httpx.Client, query: str, index: int, slo_ms: float) -> dict:
    started = time.perf_counter()
    try:
        resp = client.post("/api/v1/chat/complete", json={
            "user_query": query, "task_id": f"POOL#{index}", "batch_id": "pool_pair", "stream": False,
        })
        resp.raise_for_status()
        data = resp.json()
        e2e = (data.get("metrics") or {}).get("e2e_ms")
        ok = data.get("status") == "completed" and isinstance(e2e, (int, float)) and e2e <= slo_ms
        return {"index": index, "status": data.get("status"), "ok": bool(ok),
                "wall_ms": round((time.perf_counter() - started) * 1000, 1)}
    except httpx.HTTPError as exc:
        return {"index": index, "status": "transport_error", "ok": False,
                "error": str(exc)[:200],
                "wall_ms": round((time.perf_counter() - started) * 1000, 1)}


def record(label: str, base_url: str, data: Path, samples: int, concurrency: int,
           slo_ms: float, out_dir: Path) -> dict:
    queries = []
    if data.is_file():
        doc = json.loads(data.read_text(encoding="utf-8"))
        items = doc if isinstance(doc, list) else doc.get("tickets", [])
        for item in items:
            q = item.get("user_query") or item.get("query")
            if q:
                queries.append(q)
    if not queries:
        raise SystemExit(f"错误：数据文件无可用问题：{data}")

    metrics_before = metrics_after = {}
    results = []
    started_at = datetime.now().isoformat(timespec="seconds")
    with httpx.Client(base_url=base_url, timeout=120) as client:
        metrics_before = _metric_values(client)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_send, client, queries[i % len(queries)], i, slo_ms)
                       for i in range(samples)]
            results = [f.result() for f in futures]
        metrics_after = _metric_values(client)

    ok_count = sum(1 for r in results if r["ok"])
    payload = {
        "test_type": "tool_pool_pair",
        "label": label,
        "started_at": started_at,
        "base_url": base_url,
        "data": str(data),
        "samples": samples,
        "concurrency": concurrency,
        "slo_ms": slo_ms,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "overload_delta": (
            (metrics_after.get("harness_tool_overload_total") or 0)
            - (metrics_before.get("harness_tool_overload_total") or 0)
        ),
        "results": results,
        "summary": {
            "ok": ok_count,
            "total": len(results),
            "qualified_rate": round(ok_count / len(results), 4) if results else 0.0,
            "status_dist": {s: sum(1 for r in results if r["status"] == s)
                            for s in {r["status"] for r in results}},
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"pair_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写出：{out}")
    return payload


def compare(path_a: Path, path_b: Path) -> int:
    a = json.loads(path_a.read_text(encoding="utf-8"))
    b = json.loads(path_b.read_text(encoding="utf-8"))
    rows = []
    for key in ("qualified_rate", "overload_delta"):
        av = a["summary"].get(key) if key == "qualified_rate" else a.get(key)
        bv = b["summary"].get(key) if key == "qualified_rate" else b.get(key)
        rows.append({"metric": key, "a": av, "b": bv,
                     "delta": (bv - av) if isinstance(av, (int, float)) and isinstance(bv, (int, float)) else None})
    for key in METRIC_NAMES:
        av = (a.get("metrics_after") or {}).get(key)
        bv = (b.get("metrics_after") or {}).get(key)
        rows.append({"metric": key, "a": av, "b": bv,
                     "delta": (bv - av) if isinstance(av, (int, float)) and isinstance(bv, (int, float)) else None})
    print(json.dumps({"a": a.get("label"), "b": b.get("label"), "rows": rows},
                     ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="共享工具池配对采集（P2-18）")
    parser.add_argument("--show-config", action="store_true", help="只读打印当前工具池参数")
    parser.add_argument("--record", metavar="LABEL", help="记录一轮（default/low）")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="对比两份记录 JSON")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--data", default=str(PROJECT_ROOT.parent.parent / "testing/test-data/tickets.json"))
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--slo-ms", type=float, default=10000.0)
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "reports"))
    args = parser.parse_args(argv)

    if args.show_config:
        _show_config()
        return 0
    if args.compare:
        return compare(Path(args.compare[0]), Path(args.compare[1]))
    if args.record:
        record(args.record, args.base_url, Path(args.data), args.samples,
               args.concurrency, args.slo_ms, Path(args.out_dir))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
