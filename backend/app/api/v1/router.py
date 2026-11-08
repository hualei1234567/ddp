"""v1 路由聚合。

新模块在此挂载；路由文件只做协议适配，业务逻辑放 application/domain。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    adapter_callback_routes,
    annotation,
    calibrations,
    collection,
    dataset_grant_routes,
    dataset_routes,
    dataset_version_routes,
    devices,
    episodes,
    export_routes,
    ingestion_jobs,
    lineage,
    platform,
    sensors,
    training_delivery_routes,
    training_request_routes,
    uploads,
)

api_router = APIRouter()
api_router.include_router(platform.router)
api_router.include_router(devices.router)
api_router.include_router(sensors.router)
api_router.include_router(calibrations.router)
api_router.include_router(collection.router)
api_router.include_router(uploads.router)
api_router.include_router(ingestion_jobs.router)
api_router.include_router(episodes.router)
api_router.include_router(lineage.router)
api_router.include_router(annotation.router)
api_router.include_router(dataset_routes.router)
api_router.include_router(dataset_version_routes.router)
api_router.include_router(export_routes.router)
api_router.include_router(dataset_grant_routes.router)
api_router.include_router(training_request_routes.router)
api_router.include_router(training_delivery_routes.router)
api_router.include_router(adapter_callback_routes.router)
