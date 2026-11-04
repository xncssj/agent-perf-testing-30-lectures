"""流式计量模块单元测试：空块过滤、token 边界聚合与缺失口径（第 04 讲口径）。

覆盖 `harness/stream_metrics.py`（三阶段共享）：
- 协议层空内容（None / 空串）不充当首 Token，也不进 TPOT 样本，过滤数量单列；
- 空白字符是答复正文（Mock 把英文空格当独立 token 产出），必须保留在原文与
  token 边界里，不得当空块丢弃；
- 字符粒度服务端（阶段二/三）按共享 token 边界聚合出 token 间隔；
- token 粒度服务端（阶段一）聚合结果与"相邻片段间隔"完全一致（退化等价）；
- 单 token、无有效片段、无时刻等不适用场景一律记 None，不填 0。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = PROJECT_ROOT.parent  # 案例根：共享包 harness 所在目录
for _path in (str(PROJECT_ROOT), str(CASE_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from harness.stream_metrics import (  # noqa: E402
    TOKEN_BOUNDARY_SOURCE,
    aggregate_answer_events,
    is_effective_answer,
)
from harness.tokenization import tokenize  # noqa: E402

# 与 Mock 答复同形状的样本文本（汉字 + 单号，覆盖 tokenization 的三条边界规则）
SAMPLE = "订单X003到哪了"
SAMPLE_TOKENS = ["订单", "X003到", "哪了"]

# 含英文空格的答复：空格在 Mock 口径下是独立 token（阶段一真实形状）
SPACED = "iPhone 15 Pro"


class EffectiveAnswerTest(unittest.TestCase):
    """有效片段判定。"""

    def test_protocol_empty_filtered(self):
        for content in (None, ""):
            self.assertFalse(is_effective_answer(content), msg=repr(content))

    def test_blank_chars_are_content_not_empty_block(self):
        for content in (" ", "\n", " 甲 "):
            self.assertTrue(is_effective_answer(content), msg=repr(content))


class AggregateTest(unittest.TestCase):
    """聚合口径。"""

    def test_empty_chunks_do_not_serve_as_first_token(self):
        events = [("", 10.0), ("订", 100.0), ("单", 140.0)]
        agg = aggregate_answer_events(events)
        self.assertEqual(agg["ttft_ms"], 100.0)
        self.assertEqual(agg["empty_filtered"], 1)
        self.assertEqual(agg["answer_events_total"], 3)
        self.assertEqual(agg["answer_events_effective"], 2)
        self.assertEqual(agg["joined_text"], "订单")

    def test_blank_token_keeps_text_and_boundary(self):
        """含空格答复：原文与 token 边界完整保留，不与服务端文本失配。"""
        tokens = tokenize(SPACED)
        self.assertIn(" ", tokens)  # 空格确实是独立 token
        events = [(token, 100.0 + i * 40.0) for i, token in enumerate(tokens)]
        agg = aggregate_answer_events(events, text=SPACED)

        self.assertEqual(agg["joined_text"], SPACED)
        self.assertFalse(agg["text_mismatch"])
        self.assertEqual(agg["empty_filtered"], 0)
        self.assertEqual(agg["token_count"], len(tokens))
        self.assertEqual(agg["granularity"], "token")
        self.assertEqual(agg["tpot_ms"], 40.0)

    def test_char_granularity_aggregates_to_token_intervals(self):
        """阶段二/三形态：逐字符片段 → 按 token 边界聚合。"""
        events = [(ch, 100.0 + i * 40.0) for i, ch in enumerate(SAMPLE)]
        agg = aggregate_answer_events(events)

        self.assertEqual(agg["granularity"], "char")
        self.assertEqual(agg["char_count"], len(SAMPLE))
        self.assertEqual(agg["token_count"], len(tokenize(SAMPLE)))
        self.assertEqual(agg["token_count"], len(SAMPLE_TOKENS))
        self.assertEqual(agg["ttft_ms"], 100.0)
        # token 末字符索引：1→140ms、"到"(6)→340ms、"了"(8)→420ms
        self.assertEqual(agg["token_intervals_ms"], [200.0, 80.0])
        self.assertEqual(agg["tpot_ms"], 140.0)
        # 原始片段间隔单列，只作节奏校准
        self.assertEqual(agg["char_intervals_ms"][:3], [40.0, 40.0, 40.0])

    def test_token_granularity_matches_plain_intervals(self):
        """阶段一形态：一个片段即一个 token，聚合后与直接算间隔一致。"""
        events = [(token, 100.0 + i * 40.0) for i, token in enumerate(SAMPLE_TOKENS)]
        agg = aggregate_answer_events(events)

        self.assertEqual(agg["granularity"], "token")
        self.assertEqual(agg["answer_events_effective"], len(SAMPLE_TOKENS))
        self.assertEqual(agg["token_count"], len(SAMPLE_TOKENS))
        self.assertEqual(agg["token_intervals_ms"], [40.0, 40.0])
        self.assertEqual(agg["tpot_ms"], 40.0)

    def test_single_token_has_no_tpot(self):
        agg = aggregate_answer_events([("甲", 100.0)])
        self.assertEqual(agg["token_count"], 1)
        self.assertIsNone(agg["tpot_ms"])
        self.assertEqual(agg["ttft_ms"], 100.0)

    def test_no_effective_event_reports_none_not_zero(self):
        agg = aggregate_answer_events([("", 10.0), (None, 20.0)])
        self.assertIsNone(agg["ttft_ms"])
        self.assertIsNone(agg["tpot_ms"])
        self.assertEqual(agg["token_count"], 0)
        self.assertEqual(agg["char_count"], 0)
        self.assertEqual(agg["joined_text"], "")
        self.assertEqual(agg["granularity"], "unknown")

    def test_missing_timestamps_report_none(self):
        agg = aggregate_answer_events([("订", None), ("单", None)])
        self.assertIsNone(agg["ttft_ms"])
        self.assertIsNone(agg["tpot_ms"])
        self.assertEqual(agg["token_count"], len(tokenize("订单")))

    def test_bare_content_without_timestamps(self):
        agg = aggregate_answer_events(["订单", "X003到", "哪了"])
        self.assertEqual(agg["token_count"], 3)
        self.assertIsNone(agg["tpot_ms"])

    def test_text_argument_is_reconciliation_only(self):
        events = [(ch, 100.0 + i * 40.0) for i, ch in enumerate(SAMPLE)]
        same = aggregate_answer_events(events, text=SAMPLE)
        self.assertFalse(same["text_mismatch"])
        other = aggregate_answer_events(events, text="订单X003到哪了（补充）")
        self.assertTrue(other["text_mismatch"])
        # token 边界仍以流式片段拼接为准，不受对账文本影响
        self.assertEqual(other["token_count"], len(SAMPLE_TOKENS))

    def test_units_and_boundary_source_declared(self):
        agg = aggregate_answer_events([("订", 0.0), ("单", 40.0)])
        self.assertEqual(agg["token_boundary_source"], TOKEN_BOUNDARY_SOURCE)
        self.assertIn("ms/token", agg["tpot_unit"])
        self.assertIn("不得读作 TPOT", agg["char_interval_unit"])

    def test_interval_samples_capped(self):
        events = [(ch, float(i)) for i, ch in enumerate("订单" * 200)]
        agg = aggregate_answer_events(events)
        self.assertLessEqual(len(agg["token_intervals_ms"]), 100)
        self.assertLessEqual(len(agg["char_intervals_ms"]), 100)


if __name__ == "__main__":
    unittest.main()
