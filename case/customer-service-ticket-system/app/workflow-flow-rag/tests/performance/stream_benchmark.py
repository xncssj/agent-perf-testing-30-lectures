"""流式基准脚本：SSE 采集 TTFT / TPOT / 业务结果与中断处理（课程第 12 讲 · 阶段三 Workflow+RAG）。

运行前提：服务已启动（默认 http://127.0.0.1:8002，LLM_PROVIDER=mock 无需密钥）。
用法：python -m tests.performance.stream_benchmark --samples 10
      python -m tests.performance.stream_benchmark --samples 3 --abort-after 3   # 客户端主动中断
输出：reports/stream_workflow_<时间戳>.json（含逐笔事件轨迹与客户端实测指标）

采集口径（计量实现与三阶段共享 `harness/stream_metrics.py` 同源）：
- 空块：仅指协议层空内容（None/空串）——不充当首 Token、不计 TPOT 样本、过滤数量单列；
  空白字符属于答复正文（Mock 把英文空格当独立 token 产出），保留不过滤；
- TTFT（客户端）= 请求发出到首个有效 answer 片段到达（含网络传输）；
- TPOT（客户端）= 按 `harness/tokenization.py` 的 token 边界聚合后的相邻 token 间隔均值；
  本阶段服务端答复为逐字符产出（`workflow_service._run_stream`），原始片段间隔即
  字符间隔，只作流式节奏校准，另存 `char_intervals_ms`，不得读作 TPOT；
- E2E（客户端）= 请求发出到 done 事件到达；
- 判定口径为**客户端**：E2E 与首字取客户端实测值参与判定，服务端同名字段另存
  `server_e2e_ms` / `server_first_answer_ms` 供口径对账，两者不得混表；
- 判定维度：四级判定 AND `path_ok`/`branch_ok`/`tool_ok`/`intent_ok`（与
  `tests/performance/common.py` 的 `classify` 同口径）；
- SLO 与计价默认取服务端配置（`Settings.active_slo` / `token_price_per_1k`），
  命令行可覆盖；缺失与不适用记 None，不填 0（课程第 04 讲：样本缺失不填零）。
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
from src.config.settings import Settings  # noqa: E402
from tests.performance.common import (  # noqa: E402
    SHARED_DATA_DIR,
    aggregate,
    classify,
    failed_business,
    load_tickets,
)

STREAM_ENDPOINT = "/api/v1/chat/stream"

GRANULARITY_NOTE = (
    "服务端答复为逐字符产出：TPOT 一律按 harness/tokenization.py 的确定性 token 边界"
    "聚合后计算；char_intervals_ms 为原始片段（字符）间隔，只能作流式节奏校准。"
)


def _fmt_ms(value) -> str:
    """毫秒读数格式化：缺失如实显示为不适用，不填 0。"""
    return f"{value:.1f}ms" if isinstance(value, (int, float)) else "n/a"


def _rate(flags: list) -> float | None:
    """布尔标记通过率；无样本返回 None（不适用，不填 0）。"""
    if not flags:
        return None
    return round(sum(1 for f in flags if f) / len(flags), 4)


def stream_one(
    client: httpx.Client,
    ticket,
    slo_ms: float,
    first_answer_slo_ms: float | None,
    batch_id: str,
    abort_after: int = 0,
) -> dict:
    """单笔流式业务：解析 SSE 事件，以客户端口径判定并保留服务端终态对账。"""
    t0 = time.perf_counter()
    # 逐片段记录 (content, 到达时刻ms)；空内容块由共享计量模块过滤
    answer_events: list[tuple[str, float]] = []
    done_payload: dict = {}
    t_done = None
    stream_status = "completed"
    events_log: list[str] = []
    abort_at = time.perf_counter()

    try:
        with client.stream(
            "POST",
            STREAM_ENDPOINT,
            json={
                "query": ticket.user_query,
                "execution_id": "",  # 常规压测不使用检查点续跑（critical_path 脚本承担）
                "task_id": ticket.ticket_id,
                "batch_id": batch_id,
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
                    # message 事件带 type；done 事件无 type，按 event 名归位
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
                            # 客户端主动中断：验证服务端对断连的处理（终态应为 cancelled）
                            stream_status = "client_aborted"
                            abort_at = time.perf_counter()
                            break
                    elif kind == "done":
                        t_done = time.perf_counter()
                        done_payload = dict(data)
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

    stream_agg = aggregate_answer_events(
        answer_events, text=done_payload.get("response")
    )
    # 判定用答复原文：保留空白字符；服务端终态 response 优先，缺失时用流式拼接
    client_response = stream_agg["joined_text"]
    e2e_client = (
        round((t_done - t0) * 1000.0, 3)
        if t_done is not None
        else round((time.perf_counter() - t0) * 1000.0, 3)
    )
    server_metrics = dict(done_payload.get("metrics") or {})

    # 判定输入：以服务端终态为骨架，时延换成客户端实测口径（TTFT 参与首字判定）
    result = dict(done_payload)
    result["response"] = done_payload.get("response") or client_response
    result["metrics"] = dict(server_metrics)
    result["metrics"]["first_answer_ms"] = stream_agg["ttft_ms"]
    result["metrics"]["client_e2e_ms"] = e2e_client
    result["e2e_ms"] = e2e_client

    business = classify(
        ticket,
        result,
        slo_ms=slo_ms,
        first_answer_slo_ms=first_answer_slo_ms,
    )
    business.update({
        "run_id": done_payload.get("run_id", ""),
        "task_id": ticket.ticket_id,
        "batch_id": batch_id,
        "status": done_payload.get("status"),
        "stop_reason": done_payload.get("stop_reason", ""),
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
        "response_mismatch": stream_agg["text_mismatch"],
        "events": events_log,
        "abort_after_s": round(abort_at - t0, 3) if stream_status == "client_aborted" else None,
    }
    # 服务端同名字段留档，供客户端/服务端口径对账（不得混表）
    business["server_latency"] = {
        "server_e2e_ms": server_metrics.get("e2e_ms"),
        "server_first_answer_ms": server_metrics.get("first_answer_ms"),
        "server_answer_chunks": server_metrics.get("answer_chunks"),
        "server_chunk_intervals_ms": server_metrics.get("chunk_intervals_ms"),
        "server_evidence_kind": server_metrics.get("evidence_kind"),
    }
    return business


def run(args) -> dict:
    settings = Settings()
    slo_e2e, slo_first = settings.active_slo()
    slo_ms = args.slo_ms if args.slo_ms is not None else slo_e2e
    first_answer_slo_ms = (
        args.first_answer_slo_ms
        if args.first_answer_slo_ms is not None
        else slo_first
    )
    cost_per_1k = (
        args.cost_per_1k if args.cost_per_1k is not None else settings.token_price_per_1k
    )

    tickets = load_tickets(Path(args.data))
    samples = min(args.samples, len(tickets))
    batch_id = f"stream_workflow_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    businesses = []

    t0 = time.perf_counter()
    with httpx.Client(base_url=args.server) as client:
        for i in range(samples):
            ticket = tickets[i % len(tickets)]
            business = stream_one(
                client,
                ticket,
                slo_ms=slo_ms,
                first_answer_slo_ms=first_answer_slo_ms,
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

    agg = aggregate(businesses, duration_s, cost_per_1k=cost_per_1k)
    payload = {
        "batch_id": batch_id,
        "schema_version": 2,
        "test_type": "stream_benchmark",
        "start_at": datetime.now().isoformat(timespec="seconds"),
        "args": vars(args),
        "samples": samples,
        "duration_s": round(duration_s, 3),
        # 口径声明：判定用客户端实测；服务端为字符粒度，TPOT 按共享 token 边界换算
        "latency_caliber": "client",
        "server_granularity": "char",
        "granularity_note": GRANULARITY_NOTE,
        "slo": {
            "e2e_ms": slo_ms,
            "first_answer_ms": first_answer_slo_ms,
            "cost_per_1k": cost_per_1k,
            "fingerprint": settings.to_fingerprint_fields(),
        },
        "stage3_observations": {
            "path_ok_rate": _rate([b.get("path_ok") for b in businesses]),
            "branch_ok_rate": _rate([b.get("branch_ok") for b in businesses]),
            "intent_ok_rate": _rate([b.get("intent_ok") for b in businesses]),
            "tool_ok_rate": _rate([b.get("tool_ok") for b in businesses]),
            "note": "工单未给 expected_* 时 common.py 记为不适用（True），此处按全部样本统计",
        },
        "aggregate": agg,
        "businesses": businesses,
    }
    out = settings.reports_dir / f"{batch_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"流式基准完成：{out}")
    print(json.dumps(agg, ensure_ascii=False, indent=2))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Workflow+RAG 流式基准（SSE 采集）")
    parser.add_argument("--server", default="http://127.0.0.1:8002")
    parser.add_argument("--data", default=str(SHARED_DATA_DIR / "tickets.json"))
    parser.add_argument("--samples", type=int, default=10, help="采样业务数（≤ 工单数）")
    parser.add_argument(
        "--slo-ms", type=float, default=None, help="E2E 判定 SLO；缺省取服务端配置"
    )
    parser.add_argument(
        "--first-answer-slo-ms",
        type=float,
        default=None,
        help="首字判定 SLO（TTFT 口径）；缺省取服务端配置",
    )
    parser.add_argument(
        "--cost-per-1k", type=float, default=None, help="token 单价（元/千 token）；缺省取服务端配置"
    )
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
