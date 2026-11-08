"""后端应用入口。

分层：api → application → domain ← infrastructure。
阶段 0 提供服务骨架、健康检查与身份/项目隔离基础，不包含设备业务。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.domain.identity.errors import CrossProjectAccessError


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    # fail-closed：非本地环境未接入真实认证时直接拒绝启动。
    settings.validate_runtime_security()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(CrossProjectAccessError)
    async def handle_cross_project_error(
        request: Request,
        exc: CrossProjectAccessError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "forbidden",
                    "message": "跨项目访问被拒绝",
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.get("/health", tags=["platform"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": settings.app_name,
            "version": settings.app_version,
            "env": settings.env,
        }

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
