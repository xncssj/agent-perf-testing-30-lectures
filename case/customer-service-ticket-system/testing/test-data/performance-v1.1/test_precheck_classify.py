"""precheck.classify 分类回归（007 复审补齐）：锁定可达性口径的边界行为。

口径：unreachable（静态确定不可达）只适用于答复不依赖运行时检索的用例；
政策/联系类答复文本依赖运行时检索排序（AP-POL-06 教训：预检按 reference
文档假设命中，实际排序可能不同），静态确定缺词也保持 retrieval_dependent，
由服务逐笔回放判定。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("precheck", HERE / "precheck.py")
precheck = importlib.util.module_from_spec(spec)
sys.modules["precheck"] = precheck
spec.loader.exec_module(precheck)


def make_case(family: str, fault_profile: str = "none") -> dict:
    return {"case_id": "T", "family": family,
            "perf_tags": {"fault_profile": fault_profile}}


def make_check(tool_gaps=None, fact_gaps=None, keyword_gaps=None,
               intent="order_info") -> dict:
    return {"tool_gaps": tool_gaps or [], "fact_gaps": fact_gaps or [],
            "keyword_gaps": keyword_gaps or [], "intent": intent}


class ClassifyTests(unittest.TestCase):
    def test_policy_with_keyword_gap_stays_retrieval_dependent(self):
        # 政策类静态缺词不判 unreachable：答复依赖运行时检索，交回放判定
        result = precheck.classify(
            make_case("policy"), 1,
            make_check(keyword_gaps=["售后"], intent="return_policy"))
        self.assertEqual(result, "retrieval_dependent")

    def test_contact_with_keyword_gap_stays_retrieval_dependent(self):
        result = precheck.classify(
            make_case("contact"), 1,
            make_check(keyword_gaps=["400"], intent="contact_info"))
        self.assertEqual(result, "retrieval_dependent")

    def test_order_with_keyword_gap_is_unreachable(self):
        # 非检索依赖族：静态模板+固定结构化数据确定缺词即不可达
        result = precheck.classify(
            make_case("order"), 1, make_check(keyword_gaps=["不存在的词"]))
        self.assertEqual(result, "unreachable")

    def test_tool_gap_is_unreachable(self):
        result = precheck.classify(
            make_case("order"), 1, make_check(tool_gaps=["query_order"]))
        self.assertEqual(result, "unreachable")

    def test_fact_gap_is_unreachable(self):
        case = make_case("order")
        case["expected_facts"] = {"total_amount": 2999}
        result = precheck.classify(case, 1, make_check(fact_gaps=["total_amount=2999"]))
        self.assertEqual(result, "unreachable")

    def test_fault_profile_deferred(self):
        result = precheck.classify(
            make_case("multi_agent", fault_profile="kill9"), 2, make_check())
        self.assertEqual(result, "fault_window")

    def test_clarification_is_behavior_only(self):
        result = precheck.classify(
            make_case("clarification"), 1,
            make_check(intent="clarification"))
        self.assertEqual(result, "behavior_only")


if __name__ == "__main__":
    unittest.main()
