"""评测失败案例回流路由（V52-030，蓝图 N5 / G5）。

权限：``get_write_scope``（写入回流）与 ``get_owner_scope``（查看）。
理由——回流是**外部系统**触发的数据入口，落库形态直接影响标注队列内容；
查看含失败摘要与外部 payload，按运营口径限 owner/QA。

契约：路径只进 fragments（``paths.ops.yaml`` 增补 annotation-backflow），
**不 bump openapi.yaml version**（版本由主控统一治理）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import DbSession, get_owner_scope, get_write_scope
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.application.annotation.backflow import BackflowConversionTarget
from app.domain.annotation.backflow import DEFAULT_HOURLY_LIMIT
from app.domain.identity.models import ProjectScope
from app.infrastructure.repositories.annotation_backflow import SqlBackflowRepository

router = APIRouter(
    tags=["annotation-backflow"], prefix="/projects/{project_id}/annotation-backflow"
)

WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
OwnerScope = Annotated[ProjectScope, Depends(get_owner_scope)]


class BackflowConversionIn(BaseModel):
    """可选：受理后立即转任务的目标配置（项目既有配置，非回调方提供）。"""

    sample_set_id: str = Field(min_length=1, description="目标样本集 ID")
    schema_id: str = Field(min_length=1, description="标注 Schema ID")


class BackflowIngestIn(BaseModel):
    """评测平台回调载荷：一批失败案例。"""

    cases: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=200,
        description="案例数组（1..200）；每条必含 source / case_ref / capability",
    )
    limit: int = Field(
        default=DEFAULT_HOURLY_LIMIT,
        ge=1,
        le=10_000,
        description="单 source 每小时受理上限（超出判 RATE_LIMITED）",
    )
    conversion: BackflowConversionIn | None = Field(
        default=None,
        description="可选转换目标；缺省则案例停在 RECEIVED 由运营后续转换",
    )


class BackflowDecisionOut(BaseModel):
    """单条案例的判定。"""

    source: str = Field(description="来源系统标识")
    case_ref: str = Field(description="来源系统内的案例唯一键")
    capability: str = Field(description="失败所属能力维度")
    decision: str = Field(description="CREATE_TASK / DUPLICATE_SKIPPED / RATE_LIMITED")
    reason: str = Field(description="判定原因（审计可追溯）")
    suggested_priority: str = Field(description="映射后的任务优先级 P0..P3")


class BackflowIngestOut(BaseModel):
    """一批回流的受理结果。"""

    accepted: int = Field(ge=0, description="受理（将转任务）的案例数")
    duplicated: int = Field(ge=0, description="重复跳过的案例数")
    rate_limited: int = Field(ge=0, description="超限拒绝的案例数")
    task_id: str | None = Field(
        default=None, description="转换成功时的任务 ID；未配置转换目标时为 null"
    )
    decisions: list[BackflowDecisionOut] = Field(description="逐条判定明细（与入参顺序一致）")


class BackflowCaseOut(BaseModel):
    """回流案例的运营视图。"""

    case_id: str
    source: str
    case_ref: str
    capability: str
    status: str = Field(description="RECEIVED / CONVERTED / SKIPPED_DUPLICATE / RATE_LIMITED")
    suggested_priority: str
    failure_summary: str | None = None
    task_id: str | None = None
    received_at: datetime


class BackflowCasePageOut(BaseModel):
    """回流案例列表。"""

    items: list[BackflowCaseOut]


@router.post(
    "",
    response_model=BackflowIngestOut,
    summary="受理一批评测失败案例（幂等：同 source+case_ref 重复不报错）",
)
def ingest_backflow(
    scope: WriteScope,
    payload: BackflowIngestIn,
    session: DbSession,
    use_cases: AnnotationUseCasesDep,
) -> BackflowIngestOut:
    backflow = use_cases.backflow
    conversion = None
    if payload.conversion is not None:
        from uuid import UUID

        conversion = BackflowConversionTarget(
            sample_set_id=UUID(payload.conversion.sample_set_id),
            schema_id=UUID(payload.conversion.schema_id),
        )
    batch = backflow.ingest(
        scope,
        payload.cases,
        limit=payload.limit,
        conversion=conversion,
    )
    task_id = None
    if conversion is not None and batch.accepted:
        from uuid import UUID

        from sqlalchemy import select

        from app.infrastructure.db.models.annotation_backflow_case import (
            AnnotationBackflowCase as _Model,
        )

        row = session.scalars(
            select(_Model.task_id).where(
                _Model.project_id == scope.project_id,
                _Model.status == "CONVERTED",
            )
        ).first()
        task_id = str(row) if row else None
    return BackflowIngestOut(
        accepted=batch.accepted,
        duplicated=batch.duplicated,
        rate_limited=batch.rate_limited,
        task_id=task_id,
        decisions=[
            BackflowDecisionOut(
                source=d.case.source,
                case_ref=d.case.case_ref,
                capability=d.case.capability,
                decision=d.decision.value,
                reason=d.reason,
                suggested_priority=d.case.suggested_priority.value,
            )
            for d in batch.decisions
        ],
    )


@router.get(
    "",
    response_model=BackflowCasePageOut,
    summary="回流案例列表（按接收时间倒序）",
)
def list_backflow(
    scope: OwnerScope,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200, description="上限 200"),
) -> BackflowCasePageOut:
    rows = SqlBackflowRepository(session).list_cases(scope, limit=limit)
    return BackflowCasePageOut(
        items=[
            BackflowCaseOut(
                case_id=str(row.case_id),
                source=row.source,
                case_ref=row.case_ref,
                capability=row.capability,
                status=row.status.value,
                suggested_priority=row.suggested_priority.value,
                failure_summary=row.failure_summary,
                task_id=str(row.task_id) if row.task_id else None,
                received_at=row.received_at,
            )
            for row in rows
        ]
    )
