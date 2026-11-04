"""Workflow 服务路由（v2）：/api/v1/chat/complete、/chat/stream、/flow、/health、/version。

v2 行为与阶段一/二一致（共享 Harness 设计说明 第 6.2 节）：
- complete/stream 接受可选 task_id / batch_id（评测传固定工单 ID）；
- 合法业务运行即使失败也以结构化终态返回（HTTP 200），评测必须消费 status；
- SSE：节点/工具事件实时产出（node 事件带 node_id），done 承载完整终态，
  心跳注释用于感知断连，断连取消运行并保留终态证据；
- 旧 /chat 端点保留（v1 ChatResponse 结构，供既有脚本兼容）。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

_CASE_ROOT = Path(__file__).resolve().parents[4]
if str(_CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CASE_ROOT))

from harness.contracts import RunInput  # noqa: E402

from ..models.schemas import ChatRequest, ChatResponse, HealthResponse  # noqa: E402
from ..services.workflow_service import (  # noqa: E402
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    WorkflowService,
)

router = APIRouter(prefix="/api/v1", tags=["workflow"])

_service: WorkflowService | None = None

_KEEPALIVE_INTERVAL_S = 1.0


def init_service(service: WorkflowService) -> None:
    global _service
    _service = service


def _run_input(req: ChatRequest) -> RunInput:
    return RunInput(
        query=req.query,
        conversation_id=req.execution_id or "",
        task_id=req.task_id,
        batch_id=req.batch_id,
    )


@router.post("/chat/complete")
async def chat_complete(req: ChatRequest) -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    result = await _service.arun(_run_input(req))
    return result.model_dump()


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    if _service is None:
        raise HTTPException(status_code=503, detail="服务未初始化")

    async def real_stream():
        async for chunk in _with_keepalive(_events(_run_input(req)),
                                           _KEEPALIVE_INTERVAL_S):
            yield chunk

    return StreamingResponse(real_stream(), media_type="text/event-stream")


async def _events(run_input: RunInput):
    async for event in _service.astream(run_input):
        if event.get("type") == "done":
            payload = {k: v for k, v in event.items() if k != "type"}
            yield _sse("done", payload)
        else:
            # message 事件保留 type 字段（thinking/node/tool_call/tool_result/
            # answer），node 事件额外携带 node_id
            yield _sse("message", event)


async def _with_keepalive(agen, interval_s: float):
    """为异步迭代器叠加 SSE 心跳注释；收尾时显式关闭源生成器以传递取消。

    收尾顺序（2026-10-15 断连实跑缺陷修复，与阶段一/二同一修法）：客户端断连时
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


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """v1 兼容端点：一次性结构化结果（无 Harness 终态与事件）。"""
    if _service is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    result = await _service.executor.execute_async(req.query,
                                                   execution_id=req.execution_id)
    return ChatResponse(
        request_id=req.execution_id or uuid.uuid4().hex,
        response=result.get("response") or "",
        path=result.get("path") or [],
        branch=result.get("branch") or "",
        intent=result.get("intent") or "",
        confidence=float(result.get("confidence") or 0.0),
        e2e_ms=float(result.get("e2e_ms") or 0.0),
        total_tokens=int(result.get("total_tokens") or 0),
        status=result.get("status") or "failed",
        error=result.get("error"),
        trace=result.get("trace") or {},
    )


@router.get("/flow")
async def flow_snapshot() -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    return _service.graph_snapshot()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    if _service is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    return HealthResponse(**_service.health())


@router.get("/version")
async def version() -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail="服务未初始化")
    return _service.versions()


# ---- 知识库 CRUD（写入统一经服务层串行提交，见 workflow_service._commit_knowledge）----

@router.get("/knowledge")
def list_knowledge(q: str = "", top_k: int = 5) -> dict:
    if q.strip():
        hits = _service.store.search_knowledge(q.strip(), top_k=top_k)
        return {"items": hits, "query": q.strip(), "source": "memory.search_knowledge"}
    docs = _service.store.list_knowledge()
    return {"items": docs, "query": "", "source": "memory.knowledge"}


@router.post("/knowledge")
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
    try:
        doc = _service.create_knowledge(category, title, content, keywords)
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"item": doc}


@router.put("/knowledge/{kid}")
def update_knowledge(kid: int, body: dict) -> dict:
    try:
        doc = _service.update_knowledge(
            kid,
            category=body.get("category"),
            title=body.get("title"),
            content=body.get("content"),
            keywords=body.get("keywords"),
        )
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"item": doc}


@router.delete("/knowledge/{kid}")
def delete_knowledge(kid: int) -> dict:
    try:
        _service.delete_knowledge(kid)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"success": True}


@router.post("/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...), category: str = "uploaded", chunk_size: int = 500) -> dict:
    """上传文档（PDF/MD/TXT）并分块存入知识库。

    全部分块准备完毕后整批经服务层一次提交：任一分块与现有文档重名或
    索引构建失败时整批不写入（不留部分文档）。
    """
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
        # 先准备全部分块（title + content），再整批一次提交
        prepared = []
        for i, chunk in enumerate(chunks):
            title = f"{title_stem} (第{i + 1}段)" if len(chunks) > 1 else title_stem
            prepared.append((title, chunk))

        try:
            docs = _service.create_knowledge_batch(category, prepared)
        except KnowledgeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
