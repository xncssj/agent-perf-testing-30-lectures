# 性能指标（公开脱敏版）

> 公开脱敏版：由案例内部资料裁剪而来，已移除实例 ID、IP、账号、密钥与内部路径。

## 1. SLO 目标分档

| 指标 | 生产目标 | 压测环境目标 |
|------|---------|-----------|
| TTFT (P90) | ≤ 2s | ≤ 1.5s |
| TPOT (P90) | ≤ 100ms | ≤ 80ms |
| E2E Latency (P90) | ≤ 15s | ≤ 10s |
| Goodput（合格业务/秒） | 以基线实测校准 | 以基线实测校准 |
| 业务完成率 | ≥ 95% | ≥ 98% |
| Tool Accuracy | ≥ 90% | ≥ 95% |
| Result Quality | ≥ 85% | ≥ 90% |
| 可用性 | ≥ 99.9% | ≥ 99.5% |
| 错误率 | ≤ 1% | ≤ 0.5% |
| 超时率 | ≤ 2% | ≤ 1% |
| 恢复时间 | ≤ 30s | ≤ 15s |
| 人工转接率 | ≤ 20% | ≤ 15% |
| 成本节省率 | ≥ 60% | ≥ 70% |

> Goodput 是吞吐量纲（合格业务/秒），不是百分比；目标值随并发水平变化，
> 须待基线实测（第 11、13 讲）后按并发分档填写，不预设虚构数字。

---

## 2. 核心指标

### 2.1 性能指标

| 指标名称 | 计算方式 | 说明 | 目标值 |
|---------|---------|------|-------|
| TTFT | `first_token_time - request_start_time` | 首 Token 延迟（真实流式实测口径，见第 4 节） | P90 ≤ 2s |
| TPOT | `Σ(token_gap) / (token_count - 1)` | 每 Token 生成时间（相邻 token 实测间隔均值） | P90 ≤ 100ms |
| E2E Latency | `response_complete_time - request_start_time` | 端到端时延 | P90 ≤ 15s |
| Goodput | `满足SLO的合格业务数 / 实测时长` | 有效吞吐（单位：合格业务/秒） | 以基线实测校准 |

### 2.2 质量指标

| 指标名称 | 计算方式 | 说明 | 目标值 |
|---------|---------|------|-------|
| Tool Accuracy | `正确工具选择数 / 总选择数` | 工具选择准确率 | ≥ 90% |
| Result Quality | `合格答复数 / 总答复数` | 结果合格率 | ≥ 85% |
| Human Transfer Rate | `转人工次数 / 总请求次数` | 人工转接率 | ≤ 20% |

### 2.3 稳定性指标

| 指标名称 | 计算方式 | 说明 | 目标值 |
|---------|---------|------|-------|
| Availability | `正常运行时间 / 总时间` | 系统可用性 | ≥ 99.9% |
| Error Rate | `错误请求数 / 总请求数` | 错误率 | ≤ 1% |
| Timeout Rate | `超时请求数 / 总请求数` | 超时率 | ≤ 2% |
| Recovery Time | `故障恢复时间` | 故障恢复时间 | ≤ 30s |

### 2.4 成本指标

| 指标名称 | 计算方式 | 说明 | 目标值 |
|---------|---------|------|-------|
| 成功业务成本 | `总token成本 / 合格业务数` | 每笔合格业务的成本（以合格业务为分母，失败业务成本计入浪费） | 以基线实测校准 |
| Token Usage | `input_tokens + output_tokens` | Token 消耗量 | - |
| Cost Savings | `(原成本 - 新成本) / 原成本` | 成本节省率 | ≥ 60% |

> 成本分母是合格业务而非请求数：请求成功但结果不合格的业务消耗了 token
> 却没有产生业务价值，按请求数平摊会低估真实单位成本。

> 成本节省率为业务论证项（对比人工单笔成本），人工基线未回填前不进验收门禁；
> 过渡期“省”字验收以成功业务成本与 Token 消耗预算为准（第 08 讲口径）。

---

## 3. 次要指标

| 指标名称 | 计算方式 | 说明 |
|---------|---------|------|
| Tool Call Count | 工具调用次数 | 单次业务的工具调用次数 |
| Loop Count | 循环次数 | Agent 内部循环次数 |
| Retry Count | 重试次数 | 工具调用重试次数 |

---

## 4. 指标采集

### 4.0 采集口径与证据效力（v2，2026-09-13 Harness 改造后）

v2 口径（见 Harness 测量字段与证据来源）：一般模型 SSE 提供的是文本片段，
不能据片段数还原逐 token 到达时间，因此 `ttft_ms`/`tpot_ms` 仅在有可靠
token 级时间证据时填写，本期保留字段置空；片段级实测指标
`first_answer_ms`（首个答复片段）与 `chunk_intervals_ms`（相邻片段间隔）
本期必须可测。每次运行随响应返回 `status`/`stop_reason` 终态、
`usage_source`（reported / estimated / missing / mixed）与
`evidence_kind` 证据类型，评测按终态判定，不以 HTTP 200 推断流程成功。

| 采集路径 | 片段级指标来源 | 证据效力（evidence_kind） |
|---------|-----------------|---------|
| 真实流式（`STREAM_MODE=real`，默认） | `first_answer_ms` / `chunk_intervals_ms` 服务端实测；客户端（压测端到达时刻）另记端到端口径 | mock=模拟数据；provider_stream=真实模型流 |
| 回放校准（`STREAM_MODE=replay`） | 先完整执行业务，再按记录节奏回放事件 | replay，仅用于采集器时序校准，不得作为真实流式性能证据 |
| 非流式接口（`/api/v1/chat/complete`） | 只报告实测 E2E 与用量，`ttft_ms`/`tpot_ms` 置空 | non_stream，不合成片段级指标 |

> v1 口径变更说明：v1 非流式接口曾以“决策耗时 + 配置 token 间隔”合成
> TTFT/TPOT（仅作基线对照）；v2 起不再合成，历史报告按 v1 口径保留，
> 禁止与 v2 真实测量值直接合并比较。

流式采集工具为 `tests/performance/stream_benchmark.py`：客户端 TTFT = 请求发出
到首个 answer 事件到达（含网络），相邻 answer 事件到达间隔为客户端片段间隔；
同时记录业务终态（服务端 status + 流式状态 completed / truncated /
client_aborted），`--abort-after N` 可在首笔业务第 N 个片段后主动断连，
验证服务端中断处理。

### 3.1 采集点位

```
用户请求
    │
    ▼
┌─────────────────┐
│  t0: 请求开始   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  t1: 首 Token   │ ← TTFT = t1 - t0
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌─────────┐ ┌─────────┐
│ 工具调用 │ │ LLM 调用 │
└────┬────┘ └────┬────┘
     └─────┬─────┘
           ▼
┌─────────────────┐
│  t2: 响应完成   │ ← E2E = t2 - t0
└─────────────────┘
```

### 3.2 采集代码

```python
# utils/metrics.py
import time
from dataclasses import dataclass
from typing import Optional

@dataclass
class RequestMetrics:
    """请求指标"""
    task_id: str
    start_time: float
    first_token_time: Optional[float] = None
    end_time: Optional[float] = None
    token_count: int = 0
    tool_calls: int = 0
    
    @property
    def ttft(self) -> Optional[float]:
        """首 Token 延迟"""
        if self.first_token_time:
            return self.first_token_time - self.start_time
        return None
    
    @property
    def tpot(self) -> Optional[float]:
        """每 Token 生成时间"""
        if self.first_token_time and self.end_time and self.token_count > 0:
            return (self.end_time - self.first_token_time) / self.token_count
        return None
    
    @property
    def e2e_latency(self) -> Optional[float]:
        """端到端时延"""
        if self.end_time:
            return self.end_time - self.start_time
        return None
    
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "ttft": self.ttft,
            "tpot": self.tpot,
            "e2e_latency": self.e2e_latency,
            "token_count": self.token_count,
            "tool_calls": self.tool_calls
        }

class MetricsCollector:
    """指标采集器"""
    
    def __init__(self):
        self.metrics: dict[str, RequestMetrics] = {}
    
    def start(self, task_id: str) -> RequestMetrics:
        """开始采集"""
        metrics = RequestMetrics(
            task_id=task_id,
            start_time=time.time()
        )
        self.metrics[task_id] = metrics
        return metrics
    
    def record_first_token(self, task_id: str):
        """记录首 Token"""
        if task_id in self.metrics:
            self.metrics[task_id].first_token_time = time.time()
    
    def record_tool_call(self, task_id: str):
        """记录工具调用"""
        if task_id in self.metrics:
            self.metrics[task_id].tool_calls += 1
    
    def end(self, task_id: str, token_count: int) -> RequestMetrics:
        """结束采集"""
        if task_id in self.metrics:
            metrics = self.metrics[task_id]
            metrics.end_time = time.time()
            metrics.token_count = token_count
            return metrics
        return None
```

---

## 4. 指标计算

### 4.1 分位数计算

```python
def calculate_percentile(values: list[float], percentile: float) -> float:
    """计算分位数"""
    sorted_values = sorted(values)
    index = int(len(sorted_values) * percentile / 100)
    return sorted_values[min(index, len(sorted_values) - 1)]

def calculate_stats(values: list[float]) -> dict:
    """计算统计指标"""
    return {
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
        "p50": calculate_percentile(values, 50),
        "p90": calculate_percentile(values, 90),
        "p99": calculate_percentile(values, 99)
    }
```

### 4.2 Goodput 计算

```python
def calculate_goodput(
    qualified_requests: int,
    duration_s: float
) -> float:
    """
    计算 Goodput（单位：合格业务/秒）
    Goodput = 满足 SLO 的合格业务数 / 实测时长

    合格业务 = 请求成功 且 流程完成 且 业务完成 且 结果合格 且 时延满足 SLO
    duration_s 为实测墙钟时长（循环起止），不得使用估算值（如 samples × 假设时延）。
    """
    if duration_s <= 0:
        return 0.0
    return round(qualified_requests / duration_s, 4)
```

---

## 5. 指标报告

### 5.1 报告格式

```json
{
  "test_id": "test_12345",
  "model": "gpt-4",
  "duration": 300,
  "total_requests": 1000,
  "metrics": {
    "ttft": {
      "min": 0.5,
      "max": 3.2,
      "avg": 1.2,
      "p50": 1.0,
      "p90": 1.8,
      "p99": 2.9
    },
    "e2e_latency": {
      "min": 2.1,
      "max": 15.3,
      "avg": 5.2,
      "p50": 4.5,
      "p90": 8.1,
      "p99": 12.5
    },
    "goodput": 0.44,
    "goodput_unit": "合格业务/秒",
    "success_rate": 98.5
  }
}
```

> goodput 为吞吐小数（合格业务/秒），非百分比；数值须来自实测运行结果。