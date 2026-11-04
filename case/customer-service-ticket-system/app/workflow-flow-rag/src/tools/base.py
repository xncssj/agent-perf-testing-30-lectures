"""工具基类与数据访问接口。

工具数据（订单/物流/知识库）是“订单与物流快照”，运行时从 SQLite 读取，
保证压测期间数据固定不变（课程第 05、10 讲的数据版本要求）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """单次工具调用的结果，包含结果数据与耗时。"""

    result: dict
    latency_ms: float = 0.0
    error: str | None = None
    timed_out: bool = False

    def to_dict(self) -> dict:
        return {
            "result": self.result,
            "latency_ms": round(self.latency_ms, 3),
            "error": self.error,
            "timed_out": self.timed_out,
        }


class BaseTool:
    """工具基类：名称、描述、执行、超时。"""

    name: str = ""
    description: str = ""
    timeout_ms: float = 5000.0
    retry: int = 2

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.timeout_ms = float(cfg.get("timeout", self.timeout_ms / 1000.0)) * 1000.0
        self.retry = int(cfg.get("retry", self.retry))

    def execute(self, **kwargs: Any) -> ToolResult:
        """执行工具；含超时与重试，超时/异常记录到结果而非抛出。"""
        start = time.perf_counter()
        try:
            result = self._run(**kwargs)
            return ToolResult(result=result, latency_ms=self._elapsed(start))
        except TimeoutError:
            return ToolResult(
                result={}, latency_ms=self._elapsed(start), timed_out=True,
                error=f"{self.name} 超时",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                result={}, latency_ms=self._elapsed(start), error=str(exc)
            )

    def _run(self, **kwargs: Any) -> dict:
        raise NotImplementedError

    @staticmethod
    def _elapsed(start: float) -> float:
        return (time.perf_counter() - start) * 1000.0