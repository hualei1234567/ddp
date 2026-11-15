"""后端应用入口。

分层：api → application → domain ← infrastructure。
阶段 0 提供服务骨架、健康检查与身份/项目隔离基础，不包含设备业务。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.errors import (
    annotation_error_to_app,
    collection_error_to_app,
    dataset_error_to_app,
    datasource_error_to_app,
    domain_error_to_app,
    episode_error_to_app,
    ingestion_error_to_app,
    integrity_error_to_app,
    supplier_error_to_app,
)
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.middleware.request_id import RequestIdMiddleware
from app.api.middleware.security_headers import SecurityHeadersMiddleware
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ValidationFailedError
from app.core.logging import configure_logging
from app.domain.annotation.errors import AnnotationDomainError
from app.domain.collection.errors import CollectionDomainError
from app.domain.datasets.errors import DatasetDomainError
from app.domain.datasources.errors import DataSourceDomainError
from app.domain.devices.errors import DeviceDomainError
from app.domain.episodes.errors import EpisodeDomainError
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.ingestion.errors import IngestionDomainError
from app.domain.modelops.errors import ModelOpsDomainError
from app.domain.suppliers.errors import SupplierDomainError
from app.infrastructure.rate_limit import RateLimiter, build_rate_limiter

logger = logging.getLogger(__name__)


def _log_domain_error(request: Request, exc: BaseException, error: AppError) -> None:
    """把领域异常原文写进日志（R-12 的承诺，之前没有兑现）。

    响应体走 ``_error_payload`` + 各域 ``details`` 白名单，绝不回显 ``str(exc)``；
    但"不回显"不等于"丢掉"——原文是排障的唯一线索，必须落到日志里。
    用 ``exc_info`` 之外的显式参数，避免把堆栈打进结构化日志的 message 字段。

    ``logger.exception`` 会带堆栈，对 4xx 类业务校验是噪声，故用 ``warning``；
    只有 5xx 才升级为 ``error`` 并附堆栈。
    """
    fields = {
        "request_id": getattr(request.state, "request_id", None),
        "method": request.method,
        "path": request.url.path,
        "code": error.code,
        "status": error.status_code,
    }
    if error.status_code >= 500:
        logger.error("领域异常 %s: %s", fields, exc, exc_info=exc)
    else:
        logger.warning("领域异常 %s: %s", fields, exc)


def _error_payload(request: Request, error: AppError) -> dict[str, object]:
    body: dict[str, object] = {
        "code": error.code,
        "message": error.message,
        "request_id": getattr(request.state, "request_id", None),
    }
    if error.details:
        body["details"] = error.details
    return {"error": body}


def _register_middleware(
    app: FastAPI,
    settings: Settings,
    rate_limiter: RateLimiter | None,
) -> None:
    """注册中间件。

    Starlette 中后添加的中间件位于外层，因此按“内层 → 外层”顺序添加。
    最终执行顺序：RequestId → SecurityHeaders → CORS → RateLimit → 路由。
    这样限流/预检响应同样带安全头与 request_id。
    """
    if rate_limiter is not None:
        app.add_middleware(
            RateLimitMiddleware,
            settings=settings,
            limiter=rate_limiter,
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

    # 限流后端在装配期创建一次（Redis 客户端复用连接池），关闭时释放。
    rate_limiter = build_rate_limiter(settings) if settings.rate_limit_enabled else None
    if rate_limiter is not None:
        app.add_event_handler("shutdown", rate_limiter.close)

    # 先注册中间件；Starlette 在首次请求时构建中间件栈，
    # 以确保异常处理器与后续注册的路由都处于中间件栈内。
    _register_middleware(app, settings, rate_limiter)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # AppError 是"已经是面向客户端的错误"，message 本身可回显；
        # 但 5xx 级的仍要留日志，否则服务端错误在日志里没有痕迹。
        if exc.status_code >= 500:
            _log_domain_error(request, exc, exc)
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
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(CollectionDomainError)
    async def handle_collection_domain_error(
        request: Request,
        exc: CollectionDomainError,
    ) -> JSONResponse:
        error = collection_error_to_app(exc)
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(AnnotationDomainError)
    async def handle_annotation_domain_error(
        request: Request,
        exc: AnnotationDomainError,
    ) -> JSONResponse:
        error = annotation_error_to_app(exc)
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(EpisodeDomainError)
    async def handle_episode_domain_error(
        request: Request,
        exc: EpisodeDomainError,
    ) -> JSONResponse:
        error = episode_error_to_app(exc)
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(DatasetDomainError)
    async def handle_dataset_domain_error(
        request: Request,
        exc: DatasetDomainError,
    ) -> JSONResponse:
        error = dataset_error_to_app(exc)
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(ModelOpsDomainError)
    async def handle_modelops_domain_error(
        request: Request,
        exc: ModelOpsDomainError,
    ) -> JSONResponse:
        from app.api.errors import modelops_error_to_app

        error = modelops_error_to_app(exc)
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(DataSourceDomainError)
    async def handle_datasource_domain_error(
        request: Request,
        exc: DataSourceDomainError,
    ) -> JSONResponse:
        error = datasource_error_to_app(exc)
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(SupplierDomainError)
    async def handle_supplier_domain_error(
        request: Request,
        exc: SupplierDomainError,
    ) -> JSONResponse:
        error = supplier_error_to_app(exc)
        _log_domain_error(request, exc, error)
        return JSONResponse(
            status_code=error.status_code,
            content=_error_payload(request, error),
        )

    @app.exception_handler(IngestionDomainError)
    async def handle_ingestion_domain_error(
        request: Request,
        exc: IngestionDomainError,
    ) -> JSONResponse:
        error = ingestion_error_to_app(exc)
        _log_domain_error(request, exc, error)
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
        # 请求体校验失败此前完全不留痕：客户端只收到"参数校验失败"的固定文案，
        # 服务端也不知道是哪个字段错了。这是排障最需要的场景之一，必须落日志。
        # 回显仍只给固定文案（Pydantic 的错误会带输入值，不能进响应体）。
        _log_domain_error(request, exc, error)
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
        _log_domain_error(request, exc, error)
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
