"""Mock token 边界与流式口径的单元测试（2026-09-12 待办 2a）。

Mock 此前按单字符产出流式片段却把片段当 token，采集出的 TPOT/ITL 实为
字符事件间隔。建立确定性 token 边界后，"一个流式片段 = 一个 token"成立，
TPOT/ITL 才可按 token 计。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.tokenization import CJK_PER_TOKEN, is_cjk, token_count, tokenize  # noqa: E402


class TestTokenize(unittest.TestCase):
    def test_cjk_grouped_by_pair(self):
        self.assertEqual(["您的", "订单"], tokenize("您的订单"))

    def test_ascii_run_is_single_token(self):
        self.assertEqual(["订单", "2024010100001"], tokenize("订单2024010100001"))

    def test_punctuation_is_own_token(self):
        self.assertEqual(["您好", "，", "再见", "。"], tokenize("您好，再见。"))

    def test_mixed_text_reduces_token_count(self):
        text = "您的订单 2024010100001 当前状态为已发货，预计明天送达。"
        tokens = tokenize(text)
        self.assertLess(len(tokens), len(text))
        self.assertEqual(len(tokens), token_count(text))

    def test_empty_input(self):
        self.assertEqual([], tokenize(""))
        self.assertEqual(0, token_count(""))

    def test_cjk_detection(self):
        self.assertTrue(is_cjk("汉"))
        self.assertFalse(is_cjk("A"))
        self.assertEqual(2, CJK_PER_TOKEN)


class TestMockStreamUsesTokenBoundary(unittest.TestCase):
    def test_stream_chunks_are_tokens(self):
        """Mock 流式片段应与 token 边界一致（片段数 = token 数）。"""
        import asyncio
        import time

        from harness.contracts import RunContext
        from harness.mock_provider import MockProvider
        from src.config.settings import Settings

        provider = MockProvider(Settings())
        ctx = RunContext(
            run_id="r1", task_id="t1", query="我的订单 2024010100001 现在到哪了？",
            deadline=time.monotonic() + 30.0,
        )

        async def collect():
            parts = []
            async for chunk in provider.stream_answer([], ctx):
                if chunk.content:
                    parts.append(chunk.content)
            return parts

        parts = asyncio.run(collect())
        self.assertTrue(parts)
        # 每个片段应为一个 token（长度不超过 2 个汉字，或为 ASCII 组/标点）
        for p in parts:
            cjk = sum(1 for ch in p if is_cjk(ch))
            self.assertLessEqual(cjk, CJK_PER_TOKEN)
        self.assertEqual(token_count("".join(parts)), len(parts))


if __name__ == "__main__":
    unittest.main()
