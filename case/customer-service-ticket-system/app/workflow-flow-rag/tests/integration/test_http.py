"""HTTP 集成测试（阶段三 v2）：真实回环连接验证终态、事件序列与身份。"""
from __future__ import annotations

import json
import sys
import threading
import time
import unittest
import uvicorn
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_CASE_ROOT = PROJECT_ROOT.parent.parent
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

import httpx  # noqa: E402

ORDER_ID = "2024010100001"


class _Server:
    def __init__(self):
        import os
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        env_backup = {k: os.environ.get(k) for k in
                      ("FLOW_DB_PATH", "MOCK_INTENT_LATENCY_MS",
                       "MOCK_GENERATION_LATENCY_MS", "MOCK_TOKEN_LATENCY_MS",
                       "AGENT_DB_PATH")}
        os.environ["FLOW_DB_PATH"] = str(Path(self.tmp.name) / "flow.db")
        os.environ["AGENT_DB_PATH"] = str(Path(self.tmp.name) / "agent.db")
        for k in ("MOCK_INTENT_LATENCY_MS", "MOCK_GENERATION_LATENCY_MS"):
            os.environ[k] = "10"
        os.environ["MOCK_TOKEN_LATENCY_MS"] = "1"
        try:
            from src.config.settings import Settings
            from src.main import create_app

            self.app = create_app(Settings())
        finally:
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.server = uvicorn.Config(self.app, host="127.0.0.1", port=0,
                                     log_level="error")
        self.server = uvicorn.Server(self.server)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.tmp.cleanup()


class TestHttpIntegration(unittest.TestCase):
    def setUp(self):
        self.server = _Server()
        self.client = httpx.Client(base_url=self.server.base, timeout=60)

    def tearDown(self):
        self.client.close()
        self.server.stop()

    def test_complete_endpoint_v2_contract(self):
        resp = self.client.post("/api/v1/chat/complete", json={
            "query": f"我的订单{ORDER_ID}到哪了？",
            "task_id": "T-IT-1",
            "batch_id": "B-IT",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["stop_reason"], "answered")
        self.assertEqual(data["task_id"], "T-IT-1")
        self.assertEqual(data["batch_id"], "B-IT")
        self.assertTrue(data["run_id"])
        self.assertIn("上海分拨中心", data["response"])
        trace = data["metrics"]["trace"]
        self.assertEqual(trace["path"][0], "intent_recognition")
        self.assertEqual(trace["branch"], "auto_reply")
        for ne in trace["node_executions"]:
            self.assertIn("node_id", ne)
            self.assertIn("attempts", ne)

    def test_version_endpoint(self):
        data = self.client.get("/api/v1/version").json()
        self.assertEqual(data["protocol_mode"]["orchestration"], "workflow+rag")
        self.assertTrue(data["protocol_mode"]["rag_index_version"])
        # 如实上报实际检查点后端与保留策略（默认 memory；sqlite 见 13.11/20）
        self.assertEqual(data["checkpoint"]["store"], "memory")
        self.assertTrue(data["checkpoint"]["purge_on_completed"])
        self.assertIn("fingerprint", data)

    def test_rag_provenance_via_http(self):
        data = self.client.post("/api/v1/chat/complete", json={
            "query": "退货政策是什么", "task_id": "T-IT-2",
        }).json()
        retrieval = data["metrics"]["trace"]["retrieval"]
        self.assertTrue(retrieval.get("doc_ids"))
        self.assertTrue(retrieval.get("index_version"))
        self.assertEqual(retrieval.get("corpus"), "customer-service-kb")
        self.assertTrue(data["metrics"]["trace"].get("citations"))

    def test_sse_full_flow_events(self):
        events = []
        name = ""
        with self.client.stream("POST", "/api/v1/chat/stream", json={
            "query": f"订单{ORDER_ID}到哪了",
            "task_id": "T-SSE",
            "batch_id": "B-SSE",
        }) as resp:
            self.assertEqual(resp.status_code, 200)
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    events.append({"event": name, **data})
                    if name == "done":
                        break
        types = [e.get("type") or e.get("event") for e in events]
        self.assertEqual(types[0], "thinking")
        self.assertEqual(types[-1], "done")
        self.assertIn("node", types)
        self.assertIn("tool_call", types)
        self.assertIn("answer", types)
        # node 事件携带 node_id
        node_ids = {e.get("node_id") for e in events if e.get("type") == "node"}
        self.assertIn("intent_recognition", node_ids)
        done = events[-1]
        self.assertEqual(done["status"], "completed")
        seqs = [e["seq"] for e in events if "seq" in e]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_knowledge_crud_write_semantics(self):
        """V03/V04：初始文档带数字 ID；CRUD 语义 400/404/409；写后检索可见。"""
        resp = self.client.get("/api/v1/knowledge")
        self.assertEqual(resp.status_code, 200)
        docs = resp.json()["items"]
        self.assertEqual(len(docs), 10)
        self.assertEqual([d["id"] for d in docs], list(range(1, 11)))

        # 缺必填项 → 400
        self.assertEqual(self.client.post(
            "/api/v1/knowledge", json={"title": "t", "content": "c"}).status_code, 400)
        # 成功创建 → 返回 item；重复 category:title → 409
        created = self.client.post("/api/v1/knowledge", json={
            "category": "faq", "title": "HTTP 新增文档", "content": "HTTP 写后检索测验词",
        })
        self.assertEqual(created.status_code, 200)
        item = created.json()["item"]
        self.assertEqual(item["id"], 11)
        self.assertEqual(self.client.post("/api/v1/knowledge", json={
            "category": "faq", "title": "HTTP 新增文档", "content": "其他",
        }).status_code, 409)

        # 写接口成功后：管理检索（关键词打分）与问答检索（BM25 快照）均可见
        searched = self.client.get("/api/v1/knowledge",
                                   params={"q": "HTTP 新增文档"}).json()
        self.assertIn("HTTP 新增文档", [d["title"] for d in searched["items"]])
        chat = self.client.post("/api/v1/chat/complete", json={
            "query": "HTTP 写后检索测验词退货政策", "task_id": "T-KB-HTTP",
        }).json()
        retrieval = chat["metrics"]["trace"]["retrieval"]
        self.assertIn("faq:HTTP 新增文档", retrieval["doc_ids"])

        # 更新：不存在 → 404；改名冲突 → 409 且原文档保持不变
        self.assertEqual(self.client.put("/api/v1/knowledge/9999", json={
            "title": "x"}).status_code, 404)
        conflict = self.client.put(f"/api/v1/knowledge/{item['id']}", json={
            "title": "如何申请退货"})
        self.assertEqual(conflict.status_code, 409)
        titles = {d["title"] for d in self.client.get("/api/v1/knowledge").json()["items"]}
        self.assertIn("HTTP 新增文档", titles)
        # 更新成功后新 doc_id 可检索，旧 doc_id 消失
        renamed = self.client.put(f"/api/v1/knowledge/{item['id']}", json={
            "title": "HTTP 改名文档"})
        self.assertEqual(renamed.status_code, 200)
        # 删除：不存在 → 404；成功后问答不再检索到
        self.assertEqual(self.client.delete("/api/v1/knowledge/9999").status_code, 404)
        self.assertEqual(self.client.delete(
            f"/api/v1/knowledge/{item['id']}").status_code, 200)
        remaining = self.client.get("/api/v1/knowledge").json()["items"]
        self.assertEqual(len(remaining), 10)

    def test_knowledge_upload_batch_semantics(self):
        """V06：上传多分块一次生效；重复上传重名 → 409 整批不写入。"""
        import io

        content = ("# 批量上传手册\n\n" + "上传整批回归测验词第一段。" * 40
                   + "\n\n" + "第二段独特内容标记。" * 40)
        files = {"file": ("上传整批手册.md", io.BytesIO(content.encode("utf-8")), "text/markdown")}
        resp = self.client.post("/api/v1/knowledge/upload",
                                files=files, data={"category": "uploaded"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertGreaterEqual(body["total_chunks"], 2)
        count_after = len(self.client.get("/api/v1/knowledge").json()["items"])
        self.assertEqual(count_after, 10 + body["total_chunks"])

        # 再次上传同名文件同类别：所有分块重名 → 409，无部分写入
        before_conflict = len(self.client.get("/api/v1/knowledge").json()["items"])
        dup = self.client.post("/api/v1/knowledge/upload",
                               files=files, data={"category": "uploaded"})
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(
            len(self.client.get("/api/v1/knowledge").json()["items"]),
            before_conflict)
        # 不支持格式 → 400
        bad = self.client.post("/api/v1/knowledge/upload", files={
            "file": ("x.csv", io.BytesIO(b"a,b"), "text/csv")})
        self.assertEqual(bad.status_code, 400)
