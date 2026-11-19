"""阶段 3 标注域路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    annotation_ai,
    annotation_catalog,
    annotation_overdue_routes,
    annotation_quality,
    annotation_quality_check_routes,
    annotation_results,
    annotation_work,
)

router = APIRouter()
router.include_router(annotation_catalog.router)
router.include_router(annotation_work.router)
router.include_router(annotation_overdue_routes.router)
router.include_router(annotation_results.router)
router.include_router(annotation_quality.router)
router.include_router(annotation_quality_check_routes.router)
router.include_router(annotation_ai.router)
