"""SSE 心跳包装的收尾单元测试（阶段三）：客户端断连不得抛 aclose 重入错误。

覆盖 `src/api/routes.py` 的 `_with_keepalive`：
- 消费方被取消（等价客户端断连）时，源生成器必须被关闭；
- 收尾阶段不得抛 `RuntimeError: aclose(): asynchronous generator is already running`
  ——2026-10-15 云端真实模型客户端中断验证在阶段一实跑暴露，三阶段同一份实现
  共享该缺陷，本用例与阶段一/二同源；
- 正常完成与空闲心跳两条原路径不得回归。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = PROJECT_ROOT.parent  # 案例根：共享包 harness 所在目录
for _path in (str(PROJECT_ROOT), str(CASE_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from src.api.routes import _with_keepalive  # noqa: E402


class TestKeepaliveTeardown(unittest.IsolatedAsyncioTestCase):
    """消费方取消（客户端断连）与正常完成两条路径。"""

    async def test_disconnect_closes_source_without_runtime_error(self):
        """断连时必须干净收尾：只允许 CancelledError，且源生成器被关闭。"""
        started = asyncio.Event()
        closed = {"value": False}

        async def source():
            try:
                started.set()
                await asyncio.Event().wait()  # 模拟仍在生成中的上游流
                yield "never"  # pragma: no cover - 仅占位，实际到不了
            finally:
                closed["value"] = True

        async def consume():
            async for _ in _with_keepalive(source(), 0.05):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0.12)  # 让包装器进入等待下一次迭代并在空闲中发过心跳
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed["value"], "断连后源生成器必须被关闭")

    async def test_normal_completion_passes_items_through(self):
        """正常结束时逐项透传，不额外产出心跳。"""
        async def source():
            yield "a"
            yield "b"

        chunks = [chunk async for chunk in _with_keepalive(source(), 0.05)]
        self.assertEqual(chunks, ["a", "b"])

    async def test_idle_stream_emits_keepalive_comment(self):
        """空闲超过心跳间隔时产出 SSE 注释，且不吞掉真实事件。"""
        async def source():
            await asyncio.sleep(0.12)
            yield "x"

        chunks = [chunk async for chunk in _with_keepalive(source(), 0.05)]
        self.assertIn(": keepalive\n\n", chunks)
        self.assertEqual(chunks[-1], "x")


if __name__ == "__main__":
    unittest.main()
