"""后端应用入口。

分层：api → application → domain ← infrastructure。
阶段 0 提供服务骨架、健康检查与身份/项目隔离基础，不包含设备业务。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.errors import domain_error_to_app, integrity_error_to_app
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ValidationFailedError
from app.core.logging import configure_logging
from app.domain.devices.errors import DeviceDomainError
from app.domain.identity.errors import CrossProjectAccessError
from app.infrastructure.rate_limit import build_rate_limiter


def _error_payload(request: Request, error: AppError) -> dict[str, object]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def _register_middleware(app: FastAPI, settings: Settings) -> None:
    """注册中间件。

    Starlette 中后添加的中间件位于外层，因此按“内层 → 外层”顺序添加。
    最终执行顺序：RequestId → SecurityHeaders → CORS → RateLimit → 路由。
    这样限流/预检响应同样带安全头与 request_id。
    """
    if settings.rate_limit_enabled:
        app.add_middleware(
            RateLimitMiddleware,
            settings=settings,
            limiter=build_rate_limiter(settings),
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        expose_headers=settings.cors_expose_headers,
        max_age=settings.cors_max_age_seconds,
    )
    if settings.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    # fail-closed：非本地环境安全配置不完整时直接拒绝启动。
    settings.validate_runtime_security()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 先注册中间件；Starlette 在首次请求时构建中间件栈，
    # 以确保异常处理器与后续注册的路由都处于中间件栈内。
    _register_middleware(app, settings)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, exc),
        )

    @app.exception_handler(DeviceDomainError)
    async def handle_device_domain_error(
        request: Request,
        exc: DeviceDomainError,
    ) -> JSONResponse:
        error = domain_error_to_app(exc)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        error = ValidationFailedError()
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(
        request: Request,
        exc: IntegrityError,
    ) -> JSONResponse:
        error = integrity_error_to_app(exc)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
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
