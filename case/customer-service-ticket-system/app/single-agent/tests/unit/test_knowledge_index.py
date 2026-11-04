"""知识库检索单元测试：关键词命中、口语弱命中、BM25 泛化、top_k 与空库边界。

使用临时数据库 + 共享知识库快照（test-data/knowledge-base），断言 search_knowledge
对外契约不变：返回 [{rank, category, title, content, keywords}]。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models.database import Database, load_seeds

# 共享数据在案例根 testing/test-data（基准与 test_tokenization.py 的 _CASE_ROOT 一致；
# 原指向 app/test-data，该目录不存在，曾致本文件全部用例在 setUp 失败）
SHARED_DATA_DIR = PROJECT_ROOT.parent.parent / "testing" / "test-data"


class KnowledgeIndexTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "test.db")
        orders, logistics, knowledge = load_seeds(SHARED_DATA_DIR)
        self.knowledge_n = len(knowledge)
        self.db.seed_loads(orders, logistics, knowledge)
        self.assertGreaterEqual(self.knowledge_n, 8)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def search(self, query: str, top_k: int = 3):
        return self.db.search_knowledge(query, top_k)

    def test_keyword_exact_title_returns_faq(self) -> None:
        hits = self.search("如何申请退货")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "如何申请退货")
        self.assertEqual(hits[0]["category"], "faq")
        self.assertEqual(hits[0]["rank"], 1)

    def test_refund_query_returns_refund_faq(self) -> None:
        hits = self.search("退款多久到账")
        self.assertTrue(hits)
        self.assertIn("退款", hits[0]["title"])

    def test_phone_query_returns_contact_faq(self) -> None:
        hits = self.search("客服电话")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "客服联系电话")

    def test_product_question_hits_product_doc(self) -> None:
        hits = self.search("手机能退吗")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "iPhone 15 Pro 退换说明")

    def test_spoken_variant_still_recalls_return(self) -> None:
        # 口语「怎么退」不含标准术语，走 2 字术语单字弱命中
        hits = self.search("怎么退")
        self.assertTrue(hits)
        self.assertIn("退", hits[0]["title"] + hits[0]["content"][:20])

    def test_policy_query_returns_policy_first(self) -> None:
        hits = self.search("售后保修政策")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["category"], "policies")

    def test_top_k_truncates_results(self) -> None:
        hits = self.search("如何申请退货", top_k=2)
        self.assertLessEqual(len(hits), 2)
        self.assertEqual([h["rank"] for h in hits], list(range(1, len(hits) + 1)))

    def test_doc_structure_fields(self) -> None:
        (hit,) = self.search("如何申请退货", top_k=1)
        self.assertTrue(
            {"category", "content", "keywords", "rank", "title", "doc_id"}
            .issubset(hit.keys())
        )
        self.assertIsInstance(hit["keywords"], list)
        self.assertEqual(hit["doc_id"], "faq:如何申请退货")

    def test_trace_contract_aligns_stage3(self) -> None:
        trace = self.db.search_knowledge_with_trace("如何申请退货", top_k=3)
        self.assertEqual(
            sorted(trace.keys()),
            ["corpus", "corpus_docs", "doc_ids", "hits", "index_version",
             "latency_ms", "top_k"],
        )
        self.assertEqual(trace["corpus"], "customer-service-kb")
        self.assertEqual(trace["top_k"], 3)
        self.assertEqual(trace["corpus_docs"], self.knowledge_n)
        self.assertEqual(len(trace["doc_ids"]), len(trace["hits"]))
        self.assertGreaterEqual(len(trace["index_version"]), 8)
        self.assertGreaterEqual(trace["latency_ms"], 0)
        # 版本指纹随语料变化：追加一条知识后指纹应更新
        new_bucket = Database(Path(self._tmp.name) / "trace.db")
        new_bucket.seed_loads([], [], [])
        v0 = new_bucket.search_knowledge_with_trace("退货")["index_version"]
        new_bucket._conn.execute(
            "INSERT INTO knowledge (category, title, content, keywords) VALUES (?,?,?,?)",
            ("faq", "临时入库条目", "内容占位文本", "[]"),
        )
        new_bucket._conn.commit()
        v1 = new_bucket.search_knowledge_with_trace("退货")["index_version"]
        self.assertNotEqual(v0, v1)

    def test_empty_knowledge_base_returns_empty(self) -> None:
        empty_db = Database(Path(self._tmp.name) / "empty.db")
        empty_db.seed_loads([], [], [])
        self.assertEqual(empty_db.search_knowledge("退款"), [])

    def test_rebuild_after_seed_loads(self) -> None:
        # Database 构造时知识库为空，seed_loads 后首次检索应通过惰性重建生效
        hits = self.db.search_knowledge("客服电话", top_k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "客服联系电话")


if __name__ == "__main__":
    unittest.main()