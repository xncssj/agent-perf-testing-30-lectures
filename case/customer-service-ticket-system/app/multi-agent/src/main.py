"""FastAPI 应用入口（Multi-Agent v2）。

启动：
    uvicorn src.main:app --host 0.0.0.0 --port 8000

无外部模型也可运行（LLM_PROVIDER=mock，确定性、可复现）。
接入自托管 Langfuse 观测时配置 LANGFUSE_* 环境变量（见 Langfuse 官方文档）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from starlette.responses import Response

from .api import routes
from .config.settings import Settings
from .services.agent_service import AgentService
from .utils.logger import get_logger

logger = get_logger("src.main")

LATENCY = Histogram(
    "agent_e2e_latency_seconds",
    "端到端业务耗时",
    ["path"],
    buckets=(0.5, 1, 2, 5, 10, 20, 40, 60),
)


def create_app(settings: Settings | None = None) -> FastAPI:
    service = AgentService(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        # 进程收尾：关闭 Provider 连接与工具工作池，等待观测上报完成
        await service.aclose()

    app = FastAPI(
        title="客服工单 Multi-Agent - 性能测试案例", version="2.1.0", lifespan=lifespan
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    routes.service = service
    app.include_router(routes.router)

    @app.middleware("http")
    async def observe(request: Request, call_next):
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
