"""Harness 级 Prometheus 指标（三阶段共享，单一事实源）。

三阶段服务复用同一实现，指标按抓取 job（single-agent / multi-agent /
workflow-rag）自然区分阶段，本模块不再引入阶段维度标签。

- 工具执行池族：有界槽位的在途/容量/过载观测。执行池为快速失败设计
  （槽满明确拒绝、不排队），"队列堆积"信号因此以
  「在途调用逼近槽位上限 + 过载拒绝速率」呈现，而非排队等待时长。
- 上下文族：每次模型调用前观测 Prompt 上下文的窗口占用率；
  压缩/剪枝事件计数当前 Mock 环境无对应机制，序列初始化为 0，
  接入压缩机制后自动生效（观测点先于机制建立）。
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---- 工具执行池族（由 harness.tool_executor 维护）----
TOOL_CALLS_INFLIGHT = Gauge(
    "harness_tool_calls_inflight",
    "工具执行池当前在途调用数（占用工作槽）",
)
TOOL_SLOTS_TOTAL = Gauge(
    "harness_tool_slots_total",
    "工具执行池工作槽总数（并发线程 + 等待槽）",
)
TOOL_OVERLOAD_TOTAL = Counter(
    "harness_tool_overload_total",
    "工具执行池过载拒绝计数（槽满快速失败）",
)

# ---- 上下文族（由运行器/节点在模型调用前观测）----
CONTEXT_WINDOW_OCCUPANCY = Histogram(
    "harness_context_window_occupancy_ratio",
    "模型调用前上下文窗口占用率（Prompt Token ÷ 窗口上限）",
    buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0, 1.5),
)
CONTEXT_EVENTS_TOTAL = Counter(
    "harness_context_events_total",
    "上下文事件计数（compress=压缩 / prune=剪枝）",
    ["type"],
)
# 预建序列：未发生压缩/剪枝时也导出 0 值，看板不出现 No data
CONTEXT_EVENTS_TOTAL.labels(type="compress")
CONTEXT_EVENTS_TOTAL.labels(type="prune")


def observe_context_occupancy(prompt_tokens: int, window_tokens: int) -> None:
    """记录一次模型调用的窗口占用率；窗口未配置或无用量时静默跳过。"""
    if window_tokens > 0 and prompt_tokens > 0:
        CONTEXT_WINDOW_OCCUPANCY.observe(prompt_tokens / window_tokens)


def observe_usage_context(usage, window_tokens: int) -> None:
    """按模型结果用量观测窗口占用率（Mock=估算，OpenAI=上报口径）。

    供无完整 Message 列表的调用方使用（阶段二编排/子代理、阶段三节点）。
    """
    observe_context_occupancy(
        int(getattr(usage, "prompt_tokens", 0) or 0), window_tokens
    )


# ---- 请求准入族（R3 观测增补：B11/G1 盲区）----
# admission = 请求到达 → 首个业务事件（thinking）的墙钟差。此前该段无任何
# 仪表（Trace 根时间戳即首事件，前段是空洞），是预压测中 ~632ms 前段不可
# 归因的直接原因。工具池无排队设计（见文件头注），队列压力仍以
# TOOL_CALLS_INFLIGHT 逼近 TOOL_SLOTS_TOTAL + 过载速率呈现，不另设伪 queue 指标。
REQUEST_ADMISSION = Histogram(
    "agent_request_admission_seconds",
    "请求到达→首个业务事件的准入时长（ASGI/调度/首事件前盲区归因）",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# ---- Langfuse 上报开销（R3 观测增补：B13 量化）----
# exporter flush 在请求协程外异步执行，此前无任何耗时观测；本指标把
# 「观测税」从推断变为可测量（形态排除已由 R2b 对照轮完成，此处补绝对值）。
EXPORTER_FLUSH = Histogram(
    "langfuse_exporter_flush_seconds",
    "Langfuse 批量上报（ingestion POST）耗时",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    labelnames=["result"],
)
# 预建序列：未发生失败时 error=0 也导出，看板不出现 No data
EXPORTER_FLUSH.labels(result="ok")
EXPORTER_FLUSH.labels(result="error")


def observe_request_admission(elapsed_seconds: float) -> None:
    """登记一次请求准入时长（由 RunRecorder 首个发布事件时调用，每运行一次）。"""
    REQUEST_ADMISSION.observe(max(0.0, elapsed_seconds))


def observe_exporter_flush(elapsed_seconds: float, ok: bool) -> None:
    """登记一次 Langfuse 批量上报耗时与结果。"""
    EXPORTER_FLUSH.labels(result="ok" if ok else "error").observe(
        max(0.0, elapsed_seconds)
    )
