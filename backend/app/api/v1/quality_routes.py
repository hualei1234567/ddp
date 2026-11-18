"""统一质量引擎 HTTP 路由（N4-004）。

- ``POST`` 对单个 Episode 或导入任务同步执行规则；运行只追加，重复调用会
  产生多条历史记录，不覆盖旧结论；
- ``GET`` 详情返回六类聚合；结论明细走子资源游标分页；
- ``project_id`` 只用于定位路径，授权与隔离由服务端 ``ProjectScope`` 强制。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.quality_dependencies import (
    QualityServicesDep,
    ReadScope,
    WriteScope,
)
from app.api.v1.quality_models import QualityFindingOut, QualityRunOut
from app.api.v1.quality_schemas import QualityRunCreate
from app.api.v1.schemas import PageOut
from app.application.quality.commands import NewQualityRun
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySubjectType,
)

router = APIRouter(prefix="/projects/{project_id}/quality-runs", tags=["quality"])


@router.post("", response_model=QualityRunOut, status_code=201)
def create_quality_run(
    payload: QualityRunCreate,
    scope: WriteScope,
    services: QualityServicesDep,
) -> QualityRunOut:
    """对单个 Episode 或导入任务执行一次质量检查。

    对象不存在或跨项目返回 404；未知类别/对象类型由请求模型拒绝并映射为
    ``validation_failed``。规则集版本在运行时固化到响应与审计记录。
    """
    command = NewQualityRun(
        subject_type=QualitySubjectType(payload.subject_type),
        subject_id=payload.subject_id,
        categories=tuple(payload.categories) if payload.categories else None,
    )
    run = services.runs.run(scope, command)
    return QualityRunOut.from_domain(run)


@router.get("/{run_id}", response_model=QualityRunOut)
def get_quality_run(
    run_id: UUID,
    scope: ReadScope,
    services: QualityServicesDep,
) -> QualityRunOut:
    run = services.runs.get(scope, run_id)
    return QualityRunOut.from_domain(run)


@router.get("/{run_id}/findings", response_model=PageOut[QualityFindingOut])
def list_quality_findings(
    run_id: UUID,
    scope: ReadScope,
    services: QualityServicesDep,
    category: QualityCategory | None = None,
    status: QualityFindingStatus | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[QualityFindingOut]:
    """按游标分页列出结论明细；运行不存在或跨项目返回 404。"""
    items = services.runs.list_findings(
        scope,
        run_id,
        category=category,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.finding_id)
    return PageOut(
        items=[QualityFindingOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


__all__ = ["router"]
