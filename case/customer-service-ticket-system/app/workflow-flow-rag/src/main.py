"""FastAPI 应用入口（案例三 Workflow+RAG）。

启动：
    uvicorn src.main:app --host 0.0.0.0 --port 8000

无外部模型也可运行（LLM_PROVIDER=mock，确定性、可复现）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from .api import routes
from .config.settings import Settings
from .services.workflow_service import WorkflowService
from .utils.logger import get_logger

logger = get_logger("src.main")

REQUEST_COUNT = Counter("workflow_requests_total", "Workflow 请求总数", ["path"])
LATENCY = Histogram(
    "workflow_e2e_latency_seconds",
    "端到端业务耗时",
    ["path"],
    buckets=(0.5, 1, 2, 5, 10, 20, 40, 60),
)


def create_app(settings: Settings | None = None) -> FastAPI:
    service = WorkflowService(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        # 进程收尾：关闭工具工作池，等待观测上报完成
        import asyncio

        await service.aclose()

    app = FastAPI(title="客服工单 Workflow+RAG - 性能测试案例", version="3.1.0",
                  lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "customer-service-workflow"}

    @app.get("/version")
    def version():
        return service.versions()

    routes.init_service(service)
    app.include_router(routes.router)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        REQUEST_COUNT.labels(path=request.url.path).inc()
        with LATENCY.labels(path=request.url.path).time():
            return await call_next(request)

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.state.service = service
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)