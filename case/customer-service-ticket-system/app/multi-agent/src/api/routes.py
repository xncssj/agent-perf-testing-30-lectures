"""HTTP API：健康检查、聊天（完整/流式）、版本指纹、Prometheus 指标（v2）。

行为与阶段一 v2 一致（共享 Harness 设计说明 第 6.2 节）：
- 两个聊天入口均接受可选 task_id / batch_id；由 URL 决定响应形式，
  请求体旧 stream 字段仍接受但不再切换响应形式；
- 合法业务运行即使失败也以结构化运行结果返回（HTTP 200），
  评测必须消费 status，不能以 HTTP 200 推断流程成功；
- done 事件承载完整终态；断连时服务端取消运行并保留终态证据。
- 阶段二事件在公共契约上新增 agent_plan / agent_invoke / validation
  （additive），压测端未知 type 按一般事件记录。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.contracts import RunInput  # noqa: E402

from ..models.schemas import ChatRequest  # noqa: E402

router = APIRouter()

# 全局注入的服务实例（main.py 组装后挂载）
service = None

_KEEPALIVE_INTERVAL_S = 1.0


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "customer-service-multi-agent"}


@router.get("/version")
def version() -> dict:
    return service.versions()


# ---- 知识库 CRUD ----

@router.get("/api/v1/knowledge")
def list_knowledge(q: str = "", top_k: int = 5) -> dict:
    if q.strip():
        hits = service.db.search_knowledge(q.strip(), top_k=top_k)
        return {"items": hits, "query": q.strip(), "source": "sqlite.search_knowledge"}
    docs = service.db.list_knowledge()
    return {"items": docs, "query": "", "source": "sqlite.knowledge"}


@router.post("/api/v1/knowledge")
def create_knowledge(body: dict) -> dict:
    category = str(body.get("category") or "").strip()
    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "").strip()
    keywords = body.get("keywords") or []
    if not category:
        raise HTTPException(status_code=400, detail="缺少 category")
    if not title:
        raise HTTPException(status_code=400, detail="缺少 title")
    if not content:
        raise HTTPException(status_code=400, detail="缺少 content")
    doc = service.db.create_knowledge(category, title, content, keywords)
    return {"item": doc}


@router.put("/api/v1/knowledge/{kid}")
def update_knowledge(kid: int, body: dict) -> dict:
    doc = service.db.update_knowledge(
        kid,
        category=body.get("category"),
        title=body.get("title"),
        content=body.get("content"),
        keywords=body.get("keywords"),
    )
    if not doc:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return {"item": doc}


@router.delete("/api/v1/knowledge/{kid}")
def delete_knowledge(kid: int) -> dict:
    ok = service.db.delete_knowledge(kid)
    if not ok:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return {"success": True}


@router.post("/api/v1/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...), category: str = "uploaded", chunk_size: int = 500) -> dict:
    """上传文档（PDF/MD/TXT）并分块存入知识库。"""
    import tempfile
    from pathlib import Path

    from ..services.doc_parser import parse_document, chunk_text

    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".md", ".markdown", ".txt", ".text"):
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {suffix}")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        text = parse_document(tmp_path, filename)
        if not text.strip():
            raise HTTPException(status_code=400, detail="文档内容为空")

        chunks = chunk_text(text, chunk_size=chunk_size)
        if not chunks:
            raise HTTPException(status_code=400, detail="文档分块后无内容")

        title_stem = Path(filename).stem
        docs = []
        for i, chunk in enumerate(chunks):
            title = f"{title_stem} (第{i + 1}段)" if len(chunks) > 1 else title_stem
            doc = service.db.create_knowledge(
                category=category,
                title=title,
                content=chunk,
                keywords=[],
            )
            docs.append(doc)

        return {
            "success": True,
            "filename": filename,
            "category": category,
            "total_chunks": len(chunks),
            "items": docs,
        }
    finally:
        import os
        os.unlink(tmp_path)


@router.post("/api/v1/chat/complete")
async def chat_complete(req: ChatRequest) -> dict:
    """一次性响应：保留 response/metrics 字段，新增版本、终态与身份。"""
    result = await service.arun(_run_input(req))
    return result.model_dump()


@router.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式响应：message 事件保留 type，done 事件承载完整终态。"""
    run_input = _run_input(req)
    if service.settings.stream_mode == "replay":
        # 回放校准模式：先完整执行业务，再按记录的耗时节奏回放 SSE 事件。
        # 仅用于压测采集器的时序校准，其数据不得作为真实流式性能证据。
        result = await asyncio.to_thread(service.process_input, run_input)

        async def replay_stream():
            yield _sse("message", {
                "type": "thinking",
                "content": "超管正在理解问题并规划子 Agent 任务...",
            })
            for event in service._replay_events(result):
                yield _sse("message", event)
            for call in result.get("trace", {}).get("tool_calls", []):
                await asyncio.sleep((call.get("latency_ms") or 0) / 1000.0)
            # 答复按 token 节奏输出，模拟流式效果（校准采集器计时使用）
            for ch in result.get("response", ""):
                yield _sse("message", {"type": "answer", "content": ch})
                await asyncio.sleep(service.settings.mock_token_latency_ms / 1000.0)
            done = {
                k: v for k, v in result.items()
                if k in ("schema_version", "task_id", "run_id", "batch_id",
                         "status", "stop_reason", "response", "intent", "error")
            }
            metrics = dict(result.get("metrics") or {})
            # 回放数据必须标注证据类型，不得冒充真实流式
            metrics["evidence_kind"] = "replay"
            done["metrics"] = metrics
            yield _sse("done", done)

        return StreamingResponse(replay_stream(), media_type="text/event-stream")

    # 真实流式模式：消费 Multi-Agent 运行器事件，答复边生成边输出。
    async def real_stream():
        async for chunk in _with_keepalive(_astream_events(run_input),
                                           _KEEPALIVE_INTERVAL_S):
            yield chunk

    return StreamingResponse(real_stream(), media_type="text/event-stream")


async def _astream_events(run_input: RunInput):
    async for event in service.astream(run_input):
        if event.get("type") == "done":
            payload = {k: v for k, v in event.items() if k != "type"}
            yield _sse("done", payload)
        else:
            # message 事件保留 type 字段（thinking/agent_plan/tool_call/
            # tool_result/agent_invoke/validation/answer），压测端依赖 data.type
            yield _sse("message", event)


async def _with_keepalive(agen, interval_s: float):
    """为异步迭代器叠加 SSE 心跳注释；收尾时显式关闭源生成器以传递取消。

    收尾顺序（2026-10-15 断连实跑缺陷修复，与阶段一同一修法）：客户端断连时
    源生成器仍处于 running 状态，直接 `aclose()` 会抛
    `RuntimeError: aclose(): asynchronous generator is already running`；
    必须先把未落定的迭代任务取消并等它结束，再关闭源生成器。
    """
    it = agen.__aiter__()
    next_fut = asyncio.ensure_future(it.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({next_fut}, timeout=interval_s)
            if not done:
                yield ": keepalive\n\n"
                continue
            try:
                item = next_fut.result()
            except StopAsyncIteration:
                return
            next_fut = asyncio.ensure_future(it.__anext__())
            yield item
    finally:
        await _close_stream(it, next_fut)


async def _close_stream(it, next_fut) -> None:
    """取消未落定的迭代任务并关闭源生成器；收尾阶段不向外抛异常。"""
    if not next_fut.done():
        next_fut.cancel()
    # 先等被取消的迭代任务真正结束：否则源生成器仍处于 running，aclose() 重入报错
    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
        await next_fut
    with contextlib.suppress(RuntimeError, StopAsyncIteration):
        await it.aclose()


def _run_input(req: ChatRequest) -> RunInput:
    return RunInput(
        query=req.user_query,
        task_id=req.task_id,
        conversation_id=req.conversation_id,
        batch_id=req.batch_id,
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
