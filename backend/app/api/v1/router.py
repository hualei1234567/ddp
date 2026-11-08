"""v1 路由聚合。

新模块在此挂载；路由文件只做协议适配，业务逻辑放 application/domain。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    annotation,
    calibrations,
    collection,
    devices,
    episodes,
    ingestion_jobs,
    lineage,
    platform,
    sensors,
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
