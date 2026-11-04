"""HTTP API：健康检查、聊天（完整/流式）、版本指纹、Prometheus 指标。

v2 行为（共享 Harness 设计说明 第 6.2 节）：
- 两个聊天入口均接受可选 task_id / batch_id；由 URL 决定响应形式，
  请求体旧 stream 字段仍接受但不再切换响应形式；
- 合法业务运行即使失败也以结构化运行结果返回（HTTP 200），
  评测必须消费 status，不能以 HTTP 200 推断流程成功；
- done 事件承载完整终态；断连时服务端取消运行并保留终态证据。
"""
from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..harness.contracts import RunInput
from ..models.schemas import ChatRequest

router = APIRouter()

# 全局注入的服务实例（main.py 组装后挂载）
service = None


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "customer-service-agent"}


@router.get("/version")
def version() -> dict:
    return service.versions()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    return authorization.split(" ", 1)[1].strip()


def _current_admin(authorization: str | None) -> dict:
    token = _bearer(authorization)
    user = service.db.get_admin_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效")
    return user


@router.post("/api/v1/auth/login")
def auth_login(body: dict) -> dict:
    from ..services.admin_auth import new_token, session_expiry, verify_password

    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    user = service.db.get_admin_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = new_token()
    service.db.create_admin_session(user["id"], token, session_expiry())
    return {"token": token, "user": service.db.public_admin(user)}


@router.post("/api/v1/auth/logout")
def auth_logout(authorization: str | None = Header(default=None)) -> dict:
    token = _bearer(authorization)
    service.db.delete_admin_session(token)
    return {"success": True}


@router.get("/api/v1/auth/me")
def auth_me(authorization: str | None = Header(default=None)) -> dict:
    user = _current_admin(authorization)
    return {"user": service.db.public_admin(user)}


@router.put("/api/v1/auth/profile")
def auth_profile(body: dict, authorization: str | None = Header(default=None)) -> dict:
    user = _current_admin(authorization)
    updated = service.db.update_admin_profile(
        user["id"],
        str(body.get("display_name") or "").strip() or user["display_name"],
        str(body.get("email") or "").strip(),
        str(body.get("phone") or "").strip(),
        str(body.get("title") or "").strip(),
    )
    return {"user": updated}


@router.put("/api/v1/auth/password")
def auth_password(body: dict, authorization: str | None = Header(default=None)) -> dict:
    from ..services.admin_auth import hash_password, verify_password

    user = _current_admin(authorization)
    old = str(body.get("old_password") or "")
    new = str(body.get("new_password") or "")
    if not verify_password(old, user["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    service.db.update_admin_password(user["id"], hash_password(new))
    return {"success": True}


@router.get("/api/v1/conversations")
def list_conversations() -> dict:
    return service.catalog.conversations()


@router.get("/api/v1/tickets")
def list_tickets() -> dict:
    return service.catalog.tickets()


@router.get("/api/v1/customers")
def list_customers(page: int = 1, page_size: int = 20, q: str = "") -> dict:
    return service.catalog.customers(page=page, page_size=page_size, q=q)


@router.get("/api/v1/knowledge")
def list_knowledge(q: str = "", top_k: int = 5) -> dict:
    return service.catalog.knowledge(q, top_k=top_k)


@router.post("/api/v1/knowledge")
def create_knowledge(body: dict) -> dict:
    """新建知识文档。"""
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
    """更新知识文档。"""
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
    """删除知识文档。"""
    ok = service.db.delete_knowledge(kid)
    if not ok:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    return {"success": True}


@router.post("/api/v1/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...), category: str = "uploaded", chunk_size: int = 500) -> dict:
    """上传文档（PDF/MD/TXT）并分块存入知识库。

    - category: 知识分类，默认 uploaded
    - chunk_size: 分块大小（字符数），默认 500
    """
    import tempfile
    from pathlib import Path

    from ..services.doc_parser import parse_document, chunk_text

    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".md", ".markdown", ".txt", ".text"):
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {suffix}")

    # 保存上传文件到临时目录
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB 限制
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 解析文档
        text = parse_document(tmp_path, filename)
        if not text.strip():
            raise HTTPException(status_code=400, detail="文档内容为空")

        # 分块
        chunks = chunk_text(text, chunk_size=chunk_size)
        if not chunks:
            raise HTTPException(status_code=400, detail="文档分块后无内容")

        # 存入数据库
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
        # 清理临时文件
        import os
        os.unlink(tmp_path)


@router.get("/api/v1/analytics")
def analytics() -> dict:
    return service.catalog.analytics()


@router.get("/api/v1/models")
def list_models() -> dict:
    return service.catalog.list_models()


@router.post("/api/v1/models")
def create_model(body: dict) -> dict:
    try:
        return service.catalog.add_model(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/v1/models/{model_id}")
def update_model(model_id: str, body: dict) -> dict:
    try:
        return service.catalog.update_model(model_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="模型不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/v1/models/{model_id}")
def delete_model(model_id: str) -> dict:
    service.catalog.delete_model(model_id)
    return {"success": True}


@router.post("/api/v1/models/{model_id}/activate")
def activate_model(model_id: str) -> dict:
    try:
        return service.catalog.activate_model(model_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="模型不存在") from exc


@router.post("/api/v1/chat/complete")
async def chat_complete(req: ChatRequest) -> dict:
    """一次性响应：保留 response/metrics 字段，新增版本、终态与身份。"""
    result = await service.arun(_run_input(req))
    payload = result.model_dump()
    _remember(req, payload)
    return payload


@router.post("/api/v1/chat/stream")
async def chat_stream(req: ChatRequest):
    """SSE 流式响应：message 事件保留 type，done 事件承载完整终态。"""
    run_input = _run_input(req)
    if service.settings.stream_mode == "replay":
        # 回放校准模式：先完整执行业务，再按记录的耗时节奏回放 SSE 事件。
        # 仅用于压测采集器的时序校准，其数据不得作为真实流式性能证据。
        result = await asyncio.to_thread(service.process_input, run_input)
        _remember_input(run_input, result)

        async def replay_stream():
            yield _sse("message", {
                "type": "thinking",
                "content": "正在理解您的问题并规划工具调用...",
            })
            for call in result.get("trace", {}).get("tool_calls", []):
                yield _sse("message", {
                    "type": "tool_call",
                    "tool": call.get("tool"),
                    "tool_call_id": call.get("tool_call_id"),
                    "attempt": call.get("attempt", 1),
                    "args": call.get("args") or {},
                })
                yield _sse("message", {
                    "type": "tool_result",
                    "tool": call.get("tool"),
                    "tool_call_id": call.get("tool_call_id"),
                    "attempt": call.get("attempt", 1),
                    "data": call.get("data") or {},
                    "error": call.get("error"),
                    "duration_ms": call.get("duration_ms", 0.0),
                })
                await asyncio.sleep((call.get("duration_ms") or 0) / 1000.0)
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

    # 真实流式模式：消费 Harness 运行器事件，答复边生成边输出；
    # 消费端断连由 astream 捕获并取消运行（终态保留在服务端日志与观测）。
    # 心跳注释用于在无事件期间感知断连（SSE 注释，客户端解析器忽略）。
    async def real_stream():
        async for chunk in _with_keepalive(_astream_events(run_input),
                                           _KEEPALIVE_INTERVAL_S):
            yield chunk

    return StreamingResponse(real_stream(), media_type="text/event-stream")


_KEEPALIVE_INTERVAL_S = 1.0


async def _astream_events(run_input: RunInput):
    async for event in service.astream(run_input):
        if event.get("type") == "done":
            payload = {k: v for k, v in event.items() if k != "type"}
            _remember_input(run_input, payload)
            yield _sse("done", payload)
        else:
            # message 事件保留 type 字段（thinking/tool_call/tool_result/answer），
            # 压测端依赖 data.type 识别 answer 片段与工具事件
            yield _sse("message", event)


async def _with_keepalive(agen, interval_s: float):
    """为异步迭代器叠加 SSE 心跳注释；收尾时显式关闭源生成器以传递取消。

    收尾顺序（2026-10-15 断连实跑缺陷修复）：客户端断连时源生成器仍处于
    running 状态，直接 `aclose()` 会抛
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


def _remember(req: ChatRequest, payload: dict) -> None:
    service.catalog.record_run(
        conversation_id=req.conversation_id,
        task_id=req.task_id,
        query=req.user_query,
        status=str(payload.get("status") or ""),
        stop_reason=str(payload.get("stop_reason") or ""),
        metrics=payload.get("metrics") or {},
    )


def _remember_input(run_input: RunInput, payload: dict) -> None:
    service.catalog.record_run(
        conversation_id=run_input.conversation_id,
        task_id=run_input.task_id,
        query=run_input.query,
        status=str(payload.get("status") or ""),
        stop_reason=str(payload.get("stop_reason") or ""),
        metrics=payload.get("metrics") or {},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
