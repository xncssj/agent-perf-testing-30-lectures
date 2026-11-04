# 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。
"""流式基准脚本：SSE 采集 TTFT / TPOT / 业务结果与中断处理（课程第 12 讲）。

运行前提：服务已启动（默认 http://127.0.0.1:8000，LLM_PROVIDER=mock 无需密钥）。
用法：python -m tests.performance.stream_benchmark --samples 10 --slo-ms 15000
      python -m tests.performance.stream_benchmark --samples 5 --abort-after 3   # 客户端主动中断
输出：reports/stream_<时间戳>.json（含逐笔事件轨迹与客户端实测指标）

采集口径（计量实现与三阶段共享 `harness/stream_metrics.py` 同源）：
- 空块：仅指协议层空内容（None/空串）——不充当首 Token、不计 TPOT 样本、过滤数量单列；
  空白字符属于答复正文（Mock 把英文空格当独立 token 产出），保留不过滤；
- TTFT（客户端）= 请求发出到首个有效 answer 片段到达（含网络传输）；
- TPOT（客户端）= 按 `harness/tokenization.py` 的 token 边界聚合后的相邻 token 间隔均值
  （边界规则仅适用于 Mock 口径）；逐字符到达间隔序列另存 `char_intervals_ms`
  （按字符时间轴展开、同一片段内为 0，不得读作 TPOT 或片段间隔）；
- 缺失与不适用记 None，不填 0（课程第 04 讲：样本缺失不填零）；
- E2E（客户端）= 请求发出到 done 事件到达；判定 SLO 使用客户端口径；
- 服务端 metrics（done 事件内）另行保存，用于服务端/客户端口径相互印证；
- replay 模式（STREAM_MODE=replay）仅用于采集器时序校准，其结果须标注为回放数据。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = PROJECT_ROOT.parent  # 案例根：共享包 harness 所在目录
for _path in (str(PROJECT_ROOT), str(CASE_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from harness.stream_metrics import (  # noqa: E402
    aggregate_answer_events,
    is_effective_answer,
)
from tests.performance.common import (  # noqa: E402
    SHARED_DATA_DIR,
    aggregate,
    classify,
    failed_business,
    load_tickets,
)

STREAM_ENDPOINT = "/api/v1/chat/stream"


def _fmt_ms(value) -> str:
    """毫秒读数格式化：缺失如实显示为不适用，不填 0。"""
    return f"{value:.1f}ms" if isinstance(value, (int, float)) else "n/a"


def stream_one(
    client: httpx.Client,
    query: str,
    ticket,
    slo_ms: float,
    batch_id: str,
    abort_after: int = 0,
) -> dict:
    """单笔流式业务：解析 SSE 事件并记录客户端实测指标与业务终态。"""
    t0 = time.perf_counter()
    # 逐片段记录 (content, 到达时刻ms)；空内容块由共享计量模块过滤
    answer_events: list[tuple[str, float]] = []
    t_done = None
    stream_status = "completed"
    run_status = None
    run_id = ""
    server_metrics: dict = {}
    events_log: list[str] = []
    abort_at = time.perf_counter()

    try:
        with client.stream(
            "POST",
            STREAM_ENDPOINT,
            json={
                "user_query": query,
                "task_id": ticket.ticket_id,
                "batch_id": batch_id,
                "stream": True,
            },
            timeout=120,
        ) as resp:
            if resp.status_code != 200:
                return failed_business(ticket, f"HTTP {resp.status_code}")
            event_name = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    try:
                        data = json.loads(line.split(":", 1)[1].strip())
                    except json.JSONDecodeError:
                        continue
                    kind = data.get("type", event_name or "message")
                    if kind == "answer":
                        answer_events.append(
                            (data.get("content", ""), (time.perf_counter() - t0) * 1000.0)
                        )
                        events_log.append("answer")
                        effective_n = sum(
                            1 for content, _ in answer_events
                            if is_effective_answer(content)
                        )
                        if abort_after and effective_n >= abort_after:
                            # 客户端主动中断：验证服务端对断连的处理
                            stream_status = "client_aborted"
                            abort_at = time.perf_counter()
                            break
                    elif kind == "done":
                        t_done = time.perf_counter()
                        server_metrics = data.get("metrics", {})
                        run_status = data.get("status")
                        run_id = data.get("run_id", "")
                        events_log.append("done")
                    else:
                        events_log.append(kind)
            if stream_status == "completed" and t_done is None:
                # 流结束但未收到 done：服务端截断或异常断流
                stream_status = "truncated"
    except httpx.TimeoutException as exc:
        return failed_business(ticket, f"timeout: {exc}")
    except Exception as exc:  # noqa: BLE001
        return failed_business(ticket, f"interrupted: {exc}")

    stream_agg = aggregate_answer_events(answer_events)
    # 判定用答复原文：保留空白字符，不做 strip（空白是正文的一部分）
    response = stream_agg["joined_text"]
    e2e_client = (
        round((t_done - t0) * 1000.0, 3)
        if t_done is not None
        else round((time.perf_counter() - t0) * 1000.0, 3)
    )
    metrics = {
        # 判定口径：客户端实测（用户视角，含网络）
        "e2e_ms": e2e_client,
        "ttft_client_sse_ms": stream_agg["ttft_ms"],  # 客户端 SSE 实测（契约 §3.2：与服务端首字分开命名）
        # TPOT/ITL 单位：按共享 token 边界聚合，片段间隔另见 char_intervals_ms
        "tpot_unit": stream_agg["tpot_unit"],
        "tpot_ms": stream_agg["tpot_ms"],
        "token_count": stream_agg["token_count"],
        "total_tokens": server_metrics.get("total_tokens"),
        "usage_source": server_metrics.get("usage_source"),
        "evidence_kind": server_metrics.get("evidence_kind"),
        # 服务端指标与中断信息，供口径比对与异常分析
        "server_metrics": server_metrics,
        "stream_status": stream_status,
        "tpots_client_ms": stream_agg["token_intervals_ms"],
    }
    # v2 判定消费服务端终态；中断/截断时无 done，回退 v1 文本口径
    business = classify(ticket, response, metrics, slo_ms=slo_ms, status=run_status)
    business.update({
        "run_id": run_id,
        "task_id": ticket.ticket_id,
        "batch_id": batch_id,
        "status": run_status,
        "stop_reason": server_metrics.get("stop_reason", ""),
    })
    business["stream"] = {
        "status": stream_status,
        "ttft_client_sse_ms": stream_agg["ttft_ms"],
        "e2e_client_ms": e2e_client,
        "tpot_client_ms": stream_agg["tpot_ms"],
        "token_count": stream_agg["token_count"],
        "answer_events": stream_agg["answer_events_effective"],
        "empty_filtered": stream_agg["empty_filtered"],
        "granularity": stream_agg["granularity"],
        "token_intervals_ms": stream_agg["token_intervals_ms"],
        "char_intervals_ms": stream_agg["char_intervals_ms"],
        "events": events_log,
        "abort_after_s": round(abort_at - t0, 3) if stream_status == "client_aborted" else None,
    }
    return business


def run(args) -> dict:
    tickets = load_tickets(Path(args.data))
    samples = min(args.samples, len(tickets))
    batch_id = f"stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    businesses = []

    t0 = time.perf_counter()
    with httpx.Client(base_url=args.server) as client:
        for i in range(samples):
            ticket = tickets[i % len(tickets)]
            business = stream_one(
                client,
                ticket.user_query,
                ticket,
                slo_ms=args.slo_ms,
                batch_id=batch_id,
                abort_after=args.abort_after if i == 0 else 0,
            )
            businesses.append(business)
            stream_info = business.get("stream", {})
            print(
                f"[{i+1:03d}/{samples}] {ticket.ticket_id} "
                f"status={stream_info.get('status', 'request_failed')} "
                f"ttft={_fmt_ms(stream_info.get('ttft_client_sse_ms'))} "
                f"tpot={_fmt_ms(stream_info.get('tpot_client_ms'))} "
                f"tokens={stream_info.get('token_count') or 0} "
                f"granularity={stream_info.get('granularity', 'unknown')}"
            )
    duration_s = time.perf_counter() - t0

    agg = aggregate(businesses, duration_s=duration_s)
    payload = {
        "batch_id": batch_id,
        "schema_version": 2,
        "test_type": "stream_benchmark",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "samples": samples,
        "duration_s": round(duration_s, 3),
        "aggregate": agg,
        "businesses": businesses,
    }
    out = PROJECT_ROOT / "reports" / f"{batch_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"流式基准完成：{out}")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Single Agent 流式基准（SSE 采集）")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--samples", type=int, default=10, help="采样业务数（≤ 工单数）")
    parser.add_argument("--slo-ms", type=float, default=15000.0)
    parser.add_argument(
        "--abort-after",
        type=int,
        default=0,
        help="首笔业务在第 N 个有效 answer 片段后主动断连（0=不中断），验证中断处理",
    )
    run(parser.parse_args())
    sys.exit(0)


if __name__ == "__main__":
    main()
