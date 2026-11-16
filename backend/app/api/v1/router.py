"""v1 路由聚合。

新模块在此挂载；路由文件只做协议适配，业务逻辑放 application/domain。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    adapter_callback_routes,
    annotation,
    audit_log_routes,
    calibration_profiles,
    calibrations,
    collection,
    collection_asset_routes,
    dataset_grant_routes,
    dataset_routes,
    dataset_version_routes,
    datasource_routes,
    devices,
    episodes,
    evaluation_routes,
    export_routes,
    feedback_routes,
    governance_routes,
    inbound_routes,
    inference_routes,
    ingestion_jobs,
    lineage,
    location_routes,
    model_adapter_routes,
    model_routes,
    platform,
    project_routes,
    requirement_routes,
    robots,
    sensors,
    supplier_routes,
    training_delivery_routes,
    training_request_routes,
    uploads,
)

api_router = APIRouter()
api_router.include_router(platform.router)
# 项目管理是平台级入口：``/projects`` 与 ``/projects/{project_id}`` 和
# ``/projects/{project_id}/<resource>`` 不冲突（路径参数只会匹配单段），
# 放在业务路由之前是为了保持"平台 → 业务"的阅读顺序。
api_router.include_router(project_routes.router)
api_router.include_router(robots.router)
api_router.include_router(devices.router)
api_router.include_router(sensors.router)
api_router.include_router(calibration_profiles.router)
api_router.include_router(calibrations.router)
api_router.include_router(collection.router)
api_router.include_router(collection_asset_routes.router)
api_router.include_router(location_routes.router)
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
api_router.include_router(model_routes.router)
api_router.include_router(evaluation_routes.router)
api_router.include_router(inference_routes.router)
api_router.include_router(feedback_routes.router)
api_router.include_router(requirement_routes.router)
api_router.include_router(model_adapter_routes.router)
api_router.include_router(supplier_routes.router)
api_router.include_router(inbound_routes.router)
api_router.include_router(datasource_routes.router)
api_router.include_router(audit_log_routes.router)
# 经营治理（N9）：项目配额、用量报表；写操作前置判定由各写路由显式声明依赖。
api_router.include_router(governance_routes.router)
