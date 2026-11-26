"""v1 路由聚合。

新模块在此挂载；路由文件只做协议适配，业务逻辑放 application/domain。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    adapter_callback_routes,
    annotation,
    annotation_backflow_routes,
    annotation_dedup_routes,
    annotation_honeypot_routes,
    annotation_prm,
    annotation_skill_routes,
    asset_catalog_routes,
    audit_log_routes,
    calibration_profiles,
    calibrations,
    capture_session_routes,
    collection,
    collection_asset_routes,
    dataset_grant_routes,
    dataset_routes,
    dataset_version_routes,
    datasource_routes,
    devices,
    edge_agent_routes,
    episodes,
    evaluation_routes,
    export_routes,
    feedback_routes,
    gap_candidate_routes,
    governance_routes,
    inbound_routes,
    inference_routes,
    ingestion_jobs,
    lineage,
    llm_export_routes,
    location_routes,
    model_adapter_routes,
    model_routes,
    ops_routes,
    platform,
    processing_routes,
    project_routes,
    quality_routes,
    requirement_routes,
    robots,
    sensors,
    supplier_routes,
    token_ledger_routes,
    training_delivery_routes,
    training_request_routes,
    uploads,
)

api_router = APIRouter()
api_router.include_router(platform.router)
# 项目管理是平台级入口：``/projects`` 与 ``/projects/{project_id}`` 和
# ``/projects/{project_id}/<resource>`` 不冲突（路径参数只会匹配单段），
# 放在业务路由之前是为了保持"平台 → 业务"的阅读顺序。
api_router.include_router(project_routes.me_router)
api_router.include_router(project_routes.router)
api_router.include_router(robots.router)
api_router.include_router(devices.router)
api_router.include_router(sensors.router)
api_router.include_router(calibration_profiles.router)
api_router.include_router(calibrations.router)
api_router.include_router(collection.router)
api_router.include_router(capture_session_routes.router)
api_router.include_router(collection_asset_routes.router)
api_router.include_router(location_routes.router)
api_router.include_router(uploads.router)
api_router.include_router(ingestion_jobs.router)
api_router.include_router(episodes.router)
api_router.include_router(lineage.router)
api_router.include_router(annotation.router)
# PRM 步级标注（V52-020）：只读创建/查询 + fork 纯计算预览。
api_router.include_router(annotation_prm.router)
api_router.include_router(annotation_backflow_routes.router)
api_router.include_router(annotation_dedup_routes.router)
# 黄金题熔断（V52-008，蓝图 N3）：owner 口径的暂停解除治理端点。
api_router.include_router(annotation_honeypot_routes.router)
# 技能标签写入（V52-033）：owner/本人口径的人员技能档案治理端点。
api_router.include_router(annotation_skill_routes.router)
# 统一资产目录（N5-001）：只读跨资源检索，不替代各资源列表。
api_router.include_router(asset_catalog_routes.router)
api_router.include_router(dataset_routes.router)
api_router.include_router(dataset_version_routes.router)
api_router.include_router(export_routes.router)
# LLM 训练导出（V52-037，蓝图 N4 / 阶段 4 生态对接）：复用
# data-platform/ddp_data/llm_export 的确定性 writer，计算型端点不落库。
api_router.include_router(llm_export_routes.router)
api_router.include_router(dataset_grant_routes.router)
api_router.include_router(training_request_routes.router)
api_router.include_router(training_delivery_routes.router)
api_router.include_router(adapter_callback_routes.router)
api_router.include_router(model_routes.router)
api_router.include_router(evaluation_routes.router)
api_router.include_router(inference_routes.router)
api_router.include_router(feedback_routes.router)
api_router.include_router(gap_candidate_routes.router)
api_router.include_router(requirement_routes.router)
api_router.include_router(model_adapter_routes.router)
api_router.include_router(supplier_routes.router)
api_router.include_router(inbound_routes.router)
api_router.include_router(datasource_routes.router)
api_router.include_router(audit_log_routes.router)
api_router.include_router(ops_routes.router)
# Token 成本台账（V52-010，蓝图 N6）：与 dispatch-queue 同为 owner 口径运维视图。
api_router.include_router(token_ledger_routes.router)
# 经营治理（N9）：项目配额、用量报表；写操作前置判定由各写路由显式声明依赖。
api_router.include_router(governance_routes.router)
# 数据处理运行（N4-001）：实体与状态机；八阶段编排在 N4-002。
api_router.include_router(processing_routes.router)
api_router.include_router(quality_routes.router)
api_router.include_router(edge_agent_routes.admin_router)
api_router.include_router(edge_agent_routes.agent_router)
