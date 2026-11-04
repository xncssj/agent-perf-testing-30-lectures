"""按子 Agent 独立的熔断器：连续失败 N 次后熔断，半开窗口探活。

案例一中不存在熔断概念（单一 Agent 串行）；Multi-Agent 下每个专业子 Agent
独立熔断，避免单个 Agent 故障拖垮整个业务（级联故障防线）。
"""
from __future__ import annotations

import threading
import time


class CircuitBreaker:
    """单子 Agent 熔断器。

    - 连续失败达到 threshold 后进入 open 状态（持续 window_ms）；
    - open 期间该子 Agent 被直接降级（skipped），不再消耗调用；
    - window 结束后进入半开状态，下一次调用探测恢复。
    """

    def __init__(self, threshold: int = 5, window_ms: float = 30000.0) -> None:
        self.threshold = max(1, threshold)
        self.window_ms = window_ms
        self._lock = threading.RLock()
        self._consecutive_failures = 0
        self._open_until = 0.0

    @property
    def is_open(self) -> bool:
        with self._lock:
            if time.perf_counter() >= self._open_until:
                return False
            return True

    def _remaining_ms(self) -> float:
        return max(0.0, self._open_until - time.perf_counter()) * 1000.0

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self) -> bool:
        """记录一次失败；触发熔断时返回 True 并进入 open 状态。"""
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.threshold:
                self._open_until = time.perf_counter() + self.window_ms / 1000.0
                return True
            return False

    def state(self) -> dict:
        with self._lock:
            return {
                "consecutive_failures": self._consecutive_failures,
                "open": self.is_open,
                "remaining_ms": round(self._remaining_ms(), 3),
            }