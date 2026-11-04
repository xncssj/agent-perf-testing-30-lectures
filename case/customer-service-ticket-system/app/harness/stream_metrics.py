"""流式答复事件的统一定量口径（三阶段共享，单一事实源）。

背景：各阶段服务端流式片段的粒度并不一致——阶段一按 `harness/tokenization.py`
的确定性 token 边界产出（一个片段 = 一个 token），阶段二/三为逐字符产出
（一个片段 = 一个字符）。采集脚本若直接拿片段间隔当 TPOT，阶段二/三得到的
其实是**字符事件间隔**，同名指标实为另一种粒度（2026-09-12 待办 2a 的遗留）。

本模块把片段事件统一换算到 token 边界之后再计算时延指标：

- 片段事件：`answer` 事件的 `(content, 到达时刻 ms)` 序列；
- 空块：仅指协议层空内容（`None` 或空串），不充当首 Token、不计入 TPOT 样本，
  过滤数量单列，不静默丢弃。**空白字符属于答复文本内容**（Mock 会把
  "iPhone 15 Pro" 里的空格作为独立 token 产出），不做 strip 判定，否则会
  破坏答复原文与 token 边界；
- token 边界：`harness.tokenization.tokenize`（确定性规则，与 Mock 用量口径同源）；
- token 到达时刻 = 该 token **末字符**所属片段事件的到达时刻；
- TTFT = 首个有效片段的到达时刻（客户端口径，含网络传输）；
- TPOT = 相邻 token 到达时刻差的均值，单位 ms/token，仅 token 数 > 1 时适用；
- 字符事件间隔单独保留，只作流式节奏校准，**不得读作 TPOT**。

缺失与不适用一律记 `None`，不填 0（课程第 04 讲：样本缺失不填零）。
"""
from __future__ import annotations

from typing import Any, Iterable

from .tokenization import tokenize

# 间隔样本保留上限（逐笔产物体积控制，与既有采集脚本一致）
MAX_INTERVAL_SAMPLES = 100

TOKEN_BOUNDARY_SOURCE = "harness/tokenization.py"

TPOT_UNIT = "ms/token（按 harness/tokenization.py 的确定性边界聚合）"

CHAR_INTERVAL_UNIT = (
    "ms/片段事件（字符粒度服务端下即字符间隔，只能作流式节奏校准，不得读作 TPOT）"
)


def is_effective_answer(content: Any) -> bool:
    """有效答复片段判定：只排除协议层空内容（`None` / 空串）。

    流式协议里的心跳、空内容块与角色声明都不能充当首 Token（课程第 04 讲）。
    心跳在 SSE 中是注释行，由采集脚本按行前缀忽略；这里负责空内容块。
    空白字符是答复正文的一部分（英文单词、单号之间的空格），必须保留。
    """
    if content is None:
        return False
    return str(content) != ""


def _unpack(item: Any) -> tuple[str, float | None]:
    """把 `(content, t_ms)` 二元组或裸 content 归一化。"""
    if isinstance(item, (tuple, list)):
        if not item:
            return "", None
        content = item[0]
        t_ms = item[1] if len(item) > 1 else None
    else:
        content, t_ms = item, None
    text = "" if content is None else str(content)
    try:
        stamp = None if t_ms is None else float(t_ms)
    except (TypeError, ValueError):
        stamp = None
    return text, stamp


def _intervals(times: Iterable[float | None]) -> list[float]:
    """相邻时刻差（ms），跳过缺失时刻。"""
    out: list[float] = []
    prev: float | None = None
    for t in times:
        if t is None:
            continue
        if prev is not None:
            out.append(round(t - prev, 3))
        prev = t
    return out


def _granularity(events_effective: int, token_count: int, char_count: int) -> str:
    """由有效片段数与 token/字符数的关系判定服务端片段粒度。"""
    if events_effective <= 0:
        return "unknown"
    if abs(events_effective - token_count) <= max(1, 0.2 * token_count):
        return "token"
    if abs(events_effective - char_count) <= max(1, 0.2 * char_count):
        return "char"
    return "mixed"


def aggregate_answer_events(
    events: Iterable[Any],
    text: str | None = None,
) -> dict:
    """按 token 边界聚合流式片段事件，返回 TTFT/TPOT 与口径标注。

    参数：
        events：`(content, t_ms)` 序列，t_ms 为相对请求起点的毫秒到达时刻；
                也接受裸 content（此时所有时刻缺失，时延指标记 None）。
        text：可选的服务端权威答复文本，仅用于一致性对账（`text_mismatch`）；
              token 边界与时刻映射一律基于流式片段拼接结果，避免两个来源混用。
    """
    items = list(events)
    parts: list[str] = []
    char_times: list[float | None] = []
    empty_filtered = 0
    for item in items:
        content, t_ms = _unpack(item)
        if not is_effective_answer(content):
            empty_filtered += 1
            continue
        parts.append(content)
        char_times.extend([t_ms] * len(content))

    joined = "".join(parts)
    tokens = tokenize(joined)
    char_count = len(joined)

    # token 末字符索引 → 片段到达时刻
    token_times: list[float | None] = []
    cursor = 0
    for token in tokens:
        end = cursor + len(token) - 1
        token_times.append(char_times[end] if 0 <= end < len(char_times) else None)
        cursor += len(token)

    token_intervals = _intervals(token_times)
    char_intervals = _intervals(char_times)

    ttft_ms = char_times[0] if char_times and char_times[0] is not None else None
    tpot_ms = (
        round(sum(token_intervals) / len(token_intervals), 3)
        if token_intervals
        else None
    )

    return {
        "ttft_ms": round(ttft_ms, 3) if ttft_ms is not None else None,
        "tpot_ms": tpot_ms,
        "token_intervals_ms": token_intervals[:MAX_INTERVAL_SAMPLES],
        "char_intervals_ms": char_intervals[:MAX_INTERVAL_SAMPLES],
        "answer_events_total": len(items),
        "answer_events_effective": len(parts),
        "empty_filtered": empty_filtered,
        "token_count": len(tokens),
        "char_count": char_count,
        # 判定用答复原文（流式片段拼接结果，保留空白字符）
        "joined_text": joined,
        "granularity": _granularity(len(parts), len(tokens), char_count),
        "token_boundary_source": TOKEN_BOUNDARY_SOURCE,
        "tpot_unit": TPOT_UNIT,
        "char_interval_unit": CHAR_INTERVAL_UNIT,
        "text_mismatch": bool(text is not None and str(text) != joined),
    }
