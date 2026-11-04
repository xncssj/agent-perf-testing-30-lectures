"""知识库写一致性与快照测试（课程知识库写一致性）。

覆盖：
- V03 初始文档身份与编辑：初始语料按加载顺序获得稳定数字 ID，修改/删除
  后管理接口与问答检索均反映新状态
- V04 新增与重复冲突：写后可检索；重复 category:title 拒绝且无副作用
- V05 文档版本一致：同次检索的 hits/doc_ids/corpus_docs/index_version 同源
- V06 上传整批提交：多分块一次生效；分块重名或索引构建失败整批不写入
- V07 并发与失败回滚：检索并发写入只读完整新旧版本；写成功即读到新版本；
  候选失败不改变知识库、可用 ID 与索引版本
"""
from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.contracts import RunInput  # noqa: E402
from src.config.settings import Settings  # noqa: E402
from src.models.store import DataStore  # noqa: E402
from src.services.workflow_service import (  # noqa: E402
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    WorkflowService,
)
from src.utils.version import build_run_fingerprint  # noqa: E402


def make_service() -> WorkflowService:
    import os

    os.environ.setdefault("FLOW_DB_PATH", "/tmp/harness_m3/unit_kb.db")
    return WorkflowService(Settings())


class TestKnowledgeIdentityAndWrite(unittest.TestCase):
    """V03—V06：文档身份、写后检索、冲突与整批提交（串行路径）。"""

    @classmethod
    def setUpClass(cls):
        cls.service = make_service()

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.service.aclose())

    def test_initial_docs_have_stable_ids(self):
        """V03：初始语料按 faq→policies→products 加载顺序补齐 1..10 数字 ID。

        类内其他用例可能已写入临时文档，这里只断言初始 10 个身份
        独占 1..10，不断言集合总数。
        """
        docs = self.service.store.list_knowledge()
        initial = [d for d in docs if d["id"] <= 10]
        self.assertEqual(len(initial), 10)
        self.assertEqual(sorted(d["id"] for d in initial), list(range(1, 11)))
        self.assertEqual(DataStore.doc_id(initial[0]), "faq:如何申请退货")

    def test_numeric_id_not_in_index_version(self):
        """数字 ID 不进入语料指纹：同一语料不同 ID 分配产生同一版本。"""
        from src.rag.engine import RagEngine

        docs = [dict(d) for d in self.service.store.list_knowledge()
                if d["id"] <= 10]
        engine_a = RagEngine(self.service.store)
        engine_a.publish([dict(d, doc_id=DataStore.doc_id(d)) for d in docs])
        shifted = [dict(d, id=d["id"] + 1000) for d in docs]
        engine_b = RagEngine(self.service.store)
        engine_b.publish([dict(d, doc_id=DataStore.doc_id(d)) for d in shifted])
        self.assertEqual(engine_a.index_version, engine_b.index_version)

    def test_update_then_read_everywhere(self):
        """V03：修改成功后管理接口与问答检索均反映新状态。"""
        svc = self.service
        updated = svc.update_knowledge(3, title="客服热线（更新）")
        self.assertEqual(updated["id"], 3)
        titles = {DataStore.doc_id(d) for d in svc.store.list_knowledge()}
        self.assertIn("faq:客服热线（更新）", titles)
        self.assertNotIn("faq:客服联系电话", titles)
        # 问答检索：旧 doc_id 不再出现，新 doc_id 命中
        result = svc.rag.search("官方客服电话是多少", top_k=5)
        self.assertNotIn("faq:客服联系电话", result["doc_ids"])
        self.assertIn("faq:客服热线（更新）", result["doc_ids"])
        # 还原，避免影响同类内其他用例的题集假设
        svc.update_knowledge(3, title="客服联系电话")

    def test_delete_then_not_retrievable(self):
        """V03：删除成功后问答不再检索到该文档。"""
        svc = self.service
        svc.create_knowledge("faq", "临时删除目标", "临时内容测验词")
        before = svc.store.next_knowledge_id()
        self.assertIn("faq:临时删除目标",
                      svc.rag.search("临时删除目标", top_k=5)["doc_ids"])
        target = next(d for d in svc.store.list_knowledge()
                      if d["title"] == "临时删除目标")
        svc.delete_knowledge(target["id"])
        after = svc.rag.search("临时删除目标", top_k=5)
        self.assertNotIn("faq:临时删除目标", after["doc_ids"])
        # 删除后下一个可用 ID 不回退（不复用）
        self.assertGreaterEqual(svc.store.next_knowledge_id(), before)

    def test_create_visible_after_success_and_conflict_noop(self):
        """V04：写后可检索；重复 category:title 为 409 且原状态不变。"""
        svc = self.service
        version_before = svc.rag.index_version
        count_before = len(svc.store.list_knowledge())
        doc = svc.create_knowledge("faq", "测试新增文档", "独特测验词内容")
        self.assertEqual(doc["id"], count_before + 1)
        self.assertIn("faq:测试新增文档",
                      svc.rag.search("独特测验词内容", top_k=5)["doc_ids"])
        self.assertNotEqual(svc.rag.index_version, version_before)

        with self.assertRaises(KnowledgeConflictError):
            svc.create_knowledge("faq", "测试新增文档", "另一份内容")
        # 冲突拒绝后：文档集合、索引版本、下一个可用 ID 均不变
        self.assertEqual(len(svc.store.list_knowledge()), count_before + 1)
        self.assertEqual(svc.store.next_knowledge_id(), doc["id"] + 1)
        self.assertIn("faq:测试新增文档",
                      svc.rag.search("独特测验词内容", top_k=5)["doc_ids"])
        self.assertEqual(svc.rag.index_version,
                         svc.rag.search("任意", top_k=1)["index_version"])

    def test_update_conflict_keeps_original(self):
        """V04：更新后与其他文档重名时拒绝，原 doc_id 保持不变。"""
        svc = self.service
        target = next(d for d in svc.store.list_knowledge()
                      if d["title"] == "退款到账时间")
        with self.assertRaises(KnowledgeConflictError):
            svc.update_knowledge(target["id"], title="如何申请退货")
        titles = {d["title"] for d in svc.store.list_knowledge()}
        self.assertIn("退款到账时间", titles)

    def test_update_missing_is_404_semantics(self):
        """V03/V04：更新或删除不存在的 ID 抛 KnowledgeNotFoundError。"""
        with self.assertRaises(KnowledgeNotFoundError):
            self.service.update_knowledge(99999, title="x")
        with self.assertRaises(KnowledgeNotFoundError):
            self.service.delete_knowledge(99999)

    def test_search_fields_from_one_snapshot(self):
        """V05：同次检索的 hits/doc_ids/corpus_docs/index_version 同源一致。"""
        svc = self.service
        result = svc.rag.search("退货政策", top_k=3)
        self.assertEqual(len(result["hits"]), len(result["doc_ids"]))
        # corpus_docs 等于当前文档集合大小（类内用例自建文档已清理）
        self.assertEqual(result["corpus_docs"], len(svc.store.list_knowledge()))
        self.assertTrue(result["index_version"])
        self.assertEqual(
            result["index_version"], svc.rag.index_version,
            "无写并发时检索版本应与当前发布版本一致",
        )

    def test_batch_upload_all_or_nothing(self):
        """V06：多分块整批一次生效；重名冲突时整批不写入。"""
        svc = self.service
        version_before = svc.rag.index_version
        count_before = len(svc.store.list_knowledge())
        docs = svc.create_knowledge_batch(
            "uploaded", [("批量手册 (第1段)", "批量内容第一段测验词"),
                         ("批量手册 (第2段)", "批量内容第二段测验词")])
        self.assertEqual(len(docs), 2)
        self.assertEqual(len(svc.store.list_knowledge()), count_before + 2)
        self.assertIn("uploaded:批量手册 (第2段)",
                      svc.rag.search("批量内容第二段", top_k=5)["doc_ids"])

        # 重名冲突：与刚写入的 chunk 同名 → 409 语义，整批不生效
        with self.assertRaises(KnowledgeConflictError):
            svc.create_knowledge_batch(
                "uploaded", [("批量手册 (第1段)", "内容A"),
                             ("批量手册 (第3段)", "内容B")])
        self.assertEqual(len(svc.store.list_knowledge()), count_before + 2)
        self.assertNotIn("uploaded:批量手册 (第3段)",
                         svc.rag.search("内容B", top_k=10)["doc_ids"])
        self.assertNotEqual(svc.rag.index_version, version_before)
        self.assertEqual(svc.rag.index_version,
                         svc.rag.search("批量内容第一段", top_k=1)["index_version"])


class TestFailureRollbackAndConcurrency(unittest.TestCase):
    """V06/V07：候选索引构建失败回滚与并发读写快照一致性。"""

    @classmethod
    def setUpClass(cls):
        cls.service = make_service()

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.service.aclose())

    def test_index_build_failure_rolls_back_everything(self):
        """V06/V07：候选索引构建失败 → 500 语义异常，知识库/ID/版本不变。"""
        svc = self.service
        from src.rag.index import RagIndex

        version_before = svc.rag.index_version
        count_before = len(svc.store.list_knowledge())
        next_id_before = svc.store.next_knowledge_id()
        titles_before = [d["title"] for d in svc.store.list_knowledge()]

        original_build = RagIndex.build

        def broken_build(self_idx, docs):
            raise RuntimeError("模拟候选索引构建失败")

        RagIndex.build = broken_build
        try:
            with self.assertRaises(RuntimeError):
                svc.create_knowledge("faq", "失败回滚文档", "不应留存的测验词")
        finally:
            RagIndex.build = original_build

        self.assertEqual(len(svc.store.list_knowledge()), count_before)
        self.assertEqual([d["title"] for d in svc.store.list_knowledge()],
                         titles_before)
        self.assertEqual(svc.store.next_knowledge_id(), next_id_before)
        self.assertEqual(svc.rag.index_version, version_before)
        self.assertNotIn("faq:失败回滚文档",
                         svc.rag.search("失败回滚", top_k=10)["doc_ids"])
        # 失败后写通道仍可用（写锁已释放）
        doc = svc.create_knowledge("faq", "失败后新增", "恢复写入测验词")
        self.assertIn("faq:失败后新增",
                      svc.rag.search("恢复写入测验词", top_k=5)["doc_ids"])
        svc.delete_knowledge(doc["id"])

    def test_concurrent_read_write_sees_only_complete_versions(self):
        """V07：检索并发写入时只读完整新旧版本，不混用文档数与指纹。

        读侧断言三条结构不变量：
        1. 同一 index_version 对应的 corpus_docs 唯一（版本与文档数同快照发布）；
        2. 命中文档数不超过快照文档数；
        3. 写全部完成后，下一次检索读到最终完整版本。
        """
        svc = self.service
        base_count = len(svc.store.list_knowledge())
        created: list[dict] = []
        lock = threading.Lock()
        version_docs: dict[str, int] = {}
        read_errors: list[str] = []

        def writer():
            for i in range(12):
                doc = svc.create_knowledge(
                    "faq", f"并发写入{i}", f"并发写入测验词{i}")
                with lock:
                    created.append(doc)

        stop = threading.Event()

        def reader():
            while not stop.is_set():
                result = svc.rag.search("并发写入测验词", top_k=10)
                with lock:
                    known = version_docs.get(result["index_version"])
                    if known is None:
                        version_docs[result["index_version"]] = result["corpus_docs"]
                    elif known != result["corpus_docs"]:
                        read_errors.append(
                            f"版本 {result['index_version']} 出现两种文档数："
                            f"{known} 与 {result['corpus_docs']}")
                hit_count = len(result["doc_ids"])
                if hit_count > result["corpus_docs"]:
                    read_errors.append(
                        f"命中数 {hit_count} 超过快照文档数 {result['corpus_docs']}")
                for h in result["hits"]:
                    if not h.get("content"):
                        read_errors.append("快照外文档：命中内容为空")

        wt = threading.Thread(target=writer)
        rts = [threading.Thread(target=reader) for _ in range(3)]
        wt.start()
        for t in rts:
            t.start()
        wt.join()
        stop.set()
        for t in rts:
            t.join()

        self.assertEqual(read_errors, [])
        self.assertEqual(len(created), 12)
        # 写接口成功返回后立即检索必读到新版本：corpus_docs 与当前文档集合一致，
        # 12 个并发写入的文档全部可检索
        final_docs = len(svc.store.list_knowledge())
        # top_k 覆盖全部写入文档（12 个 + 既有 10+ 个），逐一断言可检索
        result = svc.rag.search("并发写入测验词", top_k=final_docs)
        self.assertEqual(result["corpus_docs"], final_docs)
        for i in range(12):
            self.assertIn(f"faq:并发写入{i}", result["doc_ids"])
        # 清理：删除并发写入的文档，恢复题集基线
        for doc in created:
            svc.delete_knowledge(doc["id"])
        self.assertEqual(len(svc.store.list_knowledge()), base_count)
        self.assertEqual(
            svc.rag.search("退货政策", top_k=1)["index_version"],
            svc.rag.index_version,
        )

    def test_write_then_chat_retrieval_visible(self):
        """V07：写接口成功后，问答链路下一次检索读到新内容。"""
        svc = self.service

        async def run_chat(query: str) -> dict:
            events = []
            async for ev in svc._run_stream(RunInput(query=query, task_id="T-KB")):
                events.append(ev)
            return events[-1]

        # 查询含"退货"以命中知识检索意图（return_policy），新文档含独特测验词
        doc = svc.create_knowledge("faq", "写后问答回归",
                                   "写后检索回归测验词：退货政策专属说明")
        try:
            done = asyncio.run(run_chat("写后检索回归测验词退货政策"))
            retrieval = done["trace"]["retrieval"]
            self.assertIn("faq:写后问答回归", retrieval["doc_ids"])
            self.assertEqual(retrieval["corpus_docs"],
                             len(svc.store.list_knowledge()))
        finally:
            svc.delete_knowledge(doc["id"])

    def test_numeric_ids_not_in_corpus_fingerprint(self):
        """V05：进程内数字 ID 不进入语料指纹；文档增删改变索引版本。

        运行指纹基于文件与配置：写操作不改变语料 JSON 文件，
        因此运行指纹不变，而索引版本（语料指纹）随文档变化。
        """
        svc = self.service
        fp_before = build_run_fingerprint(PROJECT_ROOT, svc.settings)["fingerprint"]
        doc = svc.create_knowledge("faq", "指纹变化文档", "指纹变化测验词")
        try:
            fp_after = build_run_fingerprint(PROJECT_ROOT, svc.settings)["fingerprint"]
            self.assertEqual(fp_before, fp_after)
            version_with_doc = svc.rag.index_version
        finally:
            svc.delete_knowledge(doc["id"])
        self.assertNotEqual(svc.rag.index_version, version_with_doc)


if __name__ == "__main__":
    unittest.main()
