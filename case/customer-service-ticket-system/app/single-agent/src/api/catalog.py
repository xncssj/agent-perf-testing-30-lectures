"""工作台只读目录与模型配置：数据来自共享数据集、SQLite 与进程内运行记录。

不返回虚构 KPI。尚无运行样本时分位数为空。
模型密钥只落在 data/models.json（已被 data/ 忽略），接口响应脱敏。
选用模型只更新该文件，不热切换当前进程的 Provider。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 口径来自 课程性能指标口径 第 1 节，是目标而不是实测值
SLO_ROWS = [
    ["指标", "生产", "压测环境"],
    ["TTFT (P90)", "≤ 2s", "≤ 1.5s"],
    ["TPOT (P90)", "≤ 100ms", "≤ 80ms"],
    ["E2E (P90)", "≤ 15s", "≤ 10s"],
    ["工具准确率", "≥ 90%", "≥ 95%"],
    ["结果合格率", "≥ 85%", "≥ 90%"],
    ["错误率", "≤ 1%", "≤ 0.5%"],
    ["超时率", "≤ 2%", "≤ 1%"],
    ["人工转接率", "≤ 20%", "≤ 15%"],
    ["成本节省率", "≥ 60%", "≥ 70%"],
]

TOOL_MAP = [
    {"tool": "query_order", "uri": "GET /api/orders/{order_id}", "service": "Order Service"},
    {"tool": "query_logistics", "uri": "GET /api/logistics/{trackingNo}", "service": "Logistics Service"},
    {
        "tool": "query_logistics",
        "uri": "GET /api/logistics/order/{orderId}",
        "service": "Logistics Service（无运单号）",
    },
    {"tool": "search_knowledge", "uri": "GET /api/kb/search?q=…", "service": "Knowledge RAG"},
]

_BRANCH_COL = {
    "auto_reply": "ai",
    "clarification": "clarify",
}
_DIFF_PRI = {"easy": "normal", "medium": "high", "hard": "urgent"}


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    weight = rank - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


class Catalog:
    def __init__(self, settings, db) -> None:
        self.settings = settings
        self.db = db
        self._lock = threading.Lock()
        self._tickets: list[dict] | None = None
        # 工单数据加载态：(是否读到, source 标注, 实际目录)；_load_tickets 首调时落定
        self._tickets_origin: tuple[bool, str, str] = (
            False, "tickets.json 缺失（看板/客户库为空）", "",
        )
        self._runs: list[dict] = []
        self._overrides: dict[str, str] = {}
        self._models_path = Path(settings.db_path).parent / "models.json"

    def _resolve_tickets_path(self) -> Path | None:
        """定位 tickets.json：优先数据目录（切档后为档目录），缺失回落镜像内置共享基准。"""
        primary = Path(self.settings.data_dir) / "tickets.json"
        if primary.exists():
            return primary
        fallback = Path(self.settings.fallback_test_data_dir) / "tickets.json"
        if fallback.exists():
            logger.warning(
                "数据目录缺 tickets.json（%s），回落镜像内置共享基准：%s",
                primary, fallback,
            )
            return fallback
        return None

    def _load_tickets(self) -> list[dict]:
        if self._tickets is not None:
            return self._tickets
        path = self._resolve_tickets_path()
        if path is None:
            logger.warning(
                "tickets.json 在数据目录与回退目录均不存在：data_dir=%s fallback=%s；"
                "负载预览/客户库将为空",
                self.settings.data_dir, self.settings.fallback_test_data_dir,
            )
            self._tickets_origin = (
                False,
                "tickets.json 缺失（看板/客户库为空）",
                f"{self.settings.data_dir} 与回退目录 {self.settings.fallback_test_data_dir} 均未命中",
            )
            self._tickets = []
            return self._tickets
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._tickets = data if isinstance(data, list) else []
        if path.parent == Path(self.settings.data_dir):
            self._tickets_origin = (True, "tickets.json", str(path.parent))
        else:
            self._tickets_origin = (True, "tickets.json（回落共享基准）", str(path.parent))
        return self._tickets

    def record_run(
        self,
        *,
        conversation_id: str,
        task_id: str,
        query: str,
        status: str,
        stop_reason: str = "",
        metrics: dict | None = None,
    ) -> None:
        metrics = metrics or {}
        item = {
            "id": "RUN-" + uuid.uuid4().hex[:10],
            "conversation_id": conversation_id or "",
            "task_id": task_id or "",
            "query": query or "",
            "status": status or "unknown",
            "stop_reason": stop_reason or "",
            "e2e_ms": metrics.get("e2e_ms"),
            "first_answer_ms": metrics.get("first_answer_ms"),
            "total_tokens": metrics.get("total_tokens") or metrics.get("token_count"),
            "ts": time.strftime("%H:%M:%S"),
        }
        col = "done" if status == "completed" else "closed"
        with self._lock:
            self._runs.append(item)
            self._runs = self._runs[-200:]
            if task_id:
                self._overrides[task_id] = col

    def conversations(self) -> dict:
        items = []
        with self._lock:
            overrides = dict(self._overrides)
            runs = list(reversed(self._runs))
        for run in runs:
            items.append({
                "id": run["id"],
                "person": run["conversation_id"] or "本次运行",
                "order": "",
                "time": run["ts"],
                "unread": 0,
                "priority": "normal",
                "status": "resolved" if run["status"] == "completed" else "closed",
                "preview": run["query"],
                "task_id": run["task_id"],
                "source": "run",
            })
        for tk in self._load_tickets():
            tid = tk.get("ticket_id") or ""
            branch = tk.get("expected_branch") or ""
            if tid in overrides:
                status = "resolved" if overrides[tid] == "done" else "closed"
            elif branch == "clarification":
                status = "clarify"
            else:
                status = "ai"
            items.append({
                "id": tid,
                "person": tk.get("user_id") or tid,
                "order": tk.get("order_id") or "",
                "time": "",
                "unread": 0,
                "priority": _DIFF_PRI.get(tk.get("difficulty") or "", "normal"),
                "status": status,
                "preview": tk.get("user_query") or "",
                "task_id": tid,
                "source": "tickets",
            })
        quick = [tk.get("user_query") for tk in self._load_tickets()[:5] if tk.get("user_query")]
        loaded, tickets_source, _ = self._tickets_origin
        source = f"{tickets_source}+runs" if loaded else tickets_source
        return {"items": items, "quick": quick, "source": source,
                "tickets_loaded": loaded}

    def tickets(self) -> dict:
        with self._lock:
            overrides = dict(self._overrides)
        items = []
        for tk in self._load_tickets():
            tid = tk.get("ticket_id") or ""
            branch = tk.get("expected_branch") or ""
            st = overrides.get(tid) or _BRANCH_COL.get(branch, "todo")
            items.append({
                "id": tid,
                "q": tk.get("user_query") or "",
                "pri": _DIFF_PRI.get(tk.get("difficulty") or "", "normal"),
                "src": "tickets.json",
                "st": st,
                "sla": branch or "—",
                "user_id": tk.get("user_id") or "",
                "order_id": tk.get("order_id") or "",
                "difficulty": tk.get("difficulty") or "",
            })
        loaded, source, tickets_dir = self._tickets_origin
        return {"items": items, "source": source, "tickets_loaded": loaded,
                "tickets_dir": tickets_dir}

    def customers(self, page: int = 1, page_size: int = 20, q: str = "") -> dict:
        items = []
        for tk in self._load_tickets():
            items.append({
                "ui": tk.get("user_id") or "",
                "tid": tk.get("ticket_id") or "",
                "order_id": tk.get("order_id") or "",
                "d": tk.get("difficulty") or "",
                "q": tk.get("user_query") or "",
                "branch": tk.get("expected_branch") or "",
            })
        kw = q.strip().lower()
        if kw:
            items = [
                it for it in items
                if kw in it["ui"].lower()
                or kw in it["tid"].lower()
                or kw in it["order_id"].lower()
                or kw in it["q"].lower()
                or kw in it["d"].lower()
                or kw in it["branch"].lower()
            ]
        total = len(items)
        page = max(1, page)
        page_size = max(1, min(page_size, 500))
        start = (page - 1) * page_size
        paged = items[start:start + page_size]
        return {
            "items": paged,
            "total": total,
            "page": page,
            "page_size": page_size,
            "source": self._tickets_origin[1],
            "tickets_loaded": self._tickets_origin[0],
        }

    def knowledge(self, query: str = "", top_k: int = 3) -> dict:
        if query.strip():
            hits = self.db.search_knowledge(query.strip(), top_k=top_k)
            return {"items": hits, "query": query.strip(), "source": "sqlite.search_knowledge"}
        docs = self.db.list_knowledge()
        return {"items": docs, "query": "", "source": "sqlite.knowledge"}

    def analytics(self) -> dict:
        with self._lock:
            runs = list(self._runs)
        e2e = [float(r["e2e_ms"]) for r in runs if isinstance(r.get("e2e_ms"), (int, float))]
        first = [
            float(r["first_answer_ms"])
            for r in runs
            if isinstance(r.get("first_answer_ms"), (int, float))
        ]
        tokens = [
            float(r["total_tokens"])
            for r in runs
            if isinstance(r.get("total_tokens"), (int, float))
        ]
        completed = sum(1 for r in runs if r.get("status") == "completed")
        total = len(runs)

        def ms(v: float | None) -> str | None:
            if v is None:
                return None
            return f"{v:.0f} ms" if v < 1000 else f"{v / 1000:.2f} s"

        p90_e2e = _percentile(e2e, 90)
        p90_first = _percentile(first, 90)
        kpis = [
            {
                "lbl": "E2E 时延 (P90)",
                "val": ms(p90_e2e) or "—",
                "sub": "本次进程运行样本" if total else "尚无运行样本",
                "cls": "good" if p90_e2e is not None and p90_e2e <= 5000 else "normal",
            },
            {
                "lbl": "首片段 (P90)",
                "val": ms(p90_first) or "—",
                "sub": "first_answer_ms",
                "cls": "normal",
            },
            {
                "lbl": "业务完成率",
                "val": f"{completed / total * 100:.1f}%" if total else "—",
                "sub": f"completed {completed}/{total}",
                "cls": "good" if total and completed == total else "normal",
            },
            {
                "lbl": "单均 Token",
                "val": f"{sum(tokens) / len(tokens):.0f}" if tokens else "—",
                "sub": "仅统计带回用量的样本",
                "cls": "normal",
            },
        ]
        e2e_bars = []
        for name, p in (("P50", 50), ("P90", 90), ("P99", 99)):
            val = _percentile(e2e, p)
            e2e_bars.append({"k": name, "v": None if val is None else round(val / 1000, 3)})
        return {
            "kpis": kpis,
            "e2e": e2e_bars,
            "slo": SLO_ROWS,
            "slo_source": "course-performance-metrics",
            "sample_count": total,
            "tools": TOOL_MAP,
        }

    def list_models(self) -> dict:
        stored = self._read_models()
        runtime = {
            "provider": self.settings.llm_provider,
            "model": self.settings.model_name,
            "base_url": self.settings.llm_base_url or "",
            "note": "进程实际推理由环境变量与 agent_config.yaml 决定；选用只写入 models.json，不会热切换。",
        }
        return {"items": [self._public_model(m) for m in stored], "runtime": runtime}

    def add_model(self, body: dict) -> dict:
        name = str(body.get("name") or "").strip()
        provider = str(body.get("provider") or "mock").strip().lower()
        if not name:
            raise ValueError("缺少 name")
        if provider not in ("mock", "openai"):
            raise ValueError("provider 只能是 mock 或 openai")
        item = {
            "id": "m-" + uuid.uuid4().hex[:8],
            "name": name,
            "provider": provider,
            "base_url": str(body.get("base_url") or body.get("baseUrl") or ""),
            "model": str(body.get("model") or ""),
            "api_key": str(body.get("api_key") or body.get("apiKey") or ""),
            "active": False,
        }
        with self._lock:
            models = self._read_models()
            models.append(item)
            self._write_models(models)
        return self._public_model(item)

    def update_model(self, model_id: str, body: dict) -> dict:
        with self._lock:
            models = self._read_models()
            found = next((m for m in models if m.get("id") == model_id), None)
            if not found:
                raise KeyError(model_id)
            if "name" in body:
                found["name"] = str(body["name"]).strip() or found["name"]
            if "provider" in body:
                provider = str(body["provider"]).lower()
                if provider not in ("mock", "openai"):
                    raise ValueError("provider 只能是 mock 或 openai")
                found["provider"] = provider
            if "base_url" in body or "baseUrl" in body:
                found["base_url"] = str(body.get("base_url") or body.get("baseUrl") or "")
            if "model" in body:
                found["model"] = str(body["model"])
            incoming = body.get("api_key", body.get("apiKey"))
            if incoming and incoming != "******":
                found["api_key"] = str(incoming)
            self._write_models(models)
            return self._public_model(found)

    def delete_model(self, model_id: str) -> None:
        with self._lock:
            models = [m for m in self._read_models() if m.get("id") != model_id]
            if not any(m.get("active") for m in models) and models:
                models[0]["active"] = True
            self._write_models(models)

    def activate_model(self, model_id: str) -> dict:
        with self._lock:
            models = self._read_models()
            found = None
            for m in models:
                m["active"] = m.get("id") == model_id
                if m["active"]:
                    found = m
            if not found:
                raise KeyError(model_id)
            self._write_models(models)
            return self._public_model(found)

    def _read_models(self) -> list[dict]:
        if self._models_path.exists():
            try:
                data = json.loads(self._models_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        seed = [{
            "id": "runtime-mock",
            "name": "当前进程模型",
            "provider": self.settings.llm_provider,
            "base_url": self.settings.llm_base_url or "",
            "model": self.settings.model_name,
            "api_key": "",
            "active": True,
        }]
        self._models_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_models(seed)
        return seed

    def _write_models(self, models: list[dict]) -> None:
        self._models_path.parent.mkdir(parents=True, exist_ok=True)
        self._models_path.write_text(
            json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _public_model(m: dict) -> dict[str, Any]:
        return {
            "id": m.get("id"),
            "name": m.get("name"),
            "provider": m.get("provider"),
            "baseUrl": m.get("base_url") or "",
            "model": m.get("model") or "",
            "apiKey": "******" if m.get("api_key") else "",
            "active": bool(m.get("active")),
        }
