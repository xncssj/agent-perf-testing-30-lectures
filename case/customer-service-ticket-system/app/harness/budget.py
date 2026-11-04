"""运行预算：决策轮次、工具尝试计数、单调时钟期限。

执行规则（共享 Harness 设计说明 第 5.2 节）：
- max_iterations：每次模型决策加一；最后一轮已 finalize 时允许完成最终生成
  （最终生成不消耗轮次，由运行器在退出动作后直接执行）。
- max_tool_calls：每次实际工具尝试加一，包括自动重试；参数被拒绝不计实际执行。
- request_timeout：从 API 接受本次业务到终态，覆盖排队、模型、工具、退避和生成；
  使用单调时钟，整体 deadline 优先于再次尝试。
"""
from __future__ import annotations

import threading
import time


class BudgetExceeded(Exception):
    """预算耗尽：reason 为 max_iterations 或 max_tool_calls。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DeadlineExceeded(Exception):
    """整体请求期限已到。"""


class Budget:
    """一次运行的预算计数器（非线程安全，仅运行器协程内使用）。"""

    def __init__(
        self,
        max_iterations: int,
        max_tool_calls: int,
        timeout_s: float,
    ) -> None:
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.started_at = time.monotonic()
        self.deadline = self.started_at + timeout_s
        self.iterations_used = 0
        self.tool_attempts_used = 0
        # 多 Worker 扇出时由根对象原子分配（单循环内使用无竞争开销）
        self._lock = threading.Lock()

    # ---- 轮次 ----
    def begin_iteration(self) -> None:
        """开始一次模型决策：检查期限并消耗一轮。"""
        self.check_deadline()
        if self.iterations_used >= self.max_iterations:
            raise BudgetExceeded("max_iterations")
        self.iterations_used += 1

    @property
    def iterations_left(self) -> int:
        return max(0, self.max_iterations - self.iterations_used)

    # ---- 工具尝试 ----
    def consume_tool_attempt(self) -> int:
        """消耗一次实际工具尝试（含自动重试），返回本次尝试序号（从 1 开始）。"""
        self.check_deadline()
        if self.tool_attempts_used >= self.max_tool_calls:
            raise BudgetExceeded("max_tool_calls")
        self.tool_attempts_used += 1
        return self.tool_attempts_used

    def allocate_tool_attempt(self) -> int:
        """原子分配一次工具尝试（多 Worker 并发扇出时由根预算统一分配）。

        与 consume_tool_attempt 语义一致，额外以锁保证并发分配不超发。
        """
        with self._lock:
            self.check_deadline()
            if self.tool_attempts_used >= self.max_tool_calls:
                raise BudgetExceeded("max_tool_calls")
            self.tool_attempts_used += 1
            return self.tool_attempts_used

    @property
    def tool_attempts_left(self) -> int:
        return max(0, self.max_tool_calls - self.tool_attempts_used)

    # ---- 期限 ----
    def check_deadline(self) -> None:
        if time.monotonic() >= self.deadline:
            raise DeadlineExceeded()

    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def remaining_ms(self) -> float:
        return self.remaining_s() * 1000.0

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started_at) * 1000.0

    def counters(self) -> dict:
        """进入 trace 的预算计数快照。"""
        return {
            "max_iterations": self.max_iterations,
            "max_tool_calls": self.max_tool_calls,
            "iterations_used": self.iterations_used,
            "tool_attempts_used": self.tool_attempts_used,
            "timeout_s": round(self.deadline - self.started_at, 3),
        }
