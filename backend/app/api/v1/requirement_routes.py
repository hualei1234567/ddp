"""数据需求闭环 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_context
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.modelops_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    ModelOpsServicesDep,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.modelops_response_models import DataRequirementOut
from app.api.v1.modelops_schemas import (
    RequirementActionRequest,
    RequirementCreate,
)
from app.api.v1.schemas import PageOut
from app.application.modelops.commands import NewRequirement
from app.core.errors import ForbiddenError
from app.domain.identity.models import ProjectContext, ProjectRole

router = APIRouter(prefix="/projects/{project_id}/data-requirements", tags=["data-requirements"])


@router.get("", response_model=PageOut[DataRequirementOut])
def list_requirements(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    status: str | None = None,
    priority: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[DataRequirementOut]:
    items = services.requirements.list(
        scope,
        status=status,
        priority=priority,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.requirement_id)
    return PageOut(
        items=[DataRequirementOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=DataRequirementOut, status_code=201)
def create_requirement(
    payload: RequirementCreate,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> DataRequirementOut:
    requirement = services.requirements.create(
        scope,
        NewRequirement(
            code=payload.code,
            title=payload.title,
            source_type=payload.source_type,
            dimensions=[dimension.to_domain() for dimension in payload.dimensions],
            owner_id=payload.owner_id,
            target_episode_count=payload.target_episode_count,
            description=payload.description,
            source_feedback_ids=payload.source_feedback_ids,
            source_evaluation_ids=payload.source_evaluation_ids,
            quality_thresholds=payload.quality_thresholds,
            priority=payload.priority,
            due_at=payload.due_at,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DataRequirementOut.from_domain(requirement)


@router.get("/{requirement_id}", response_model=DataRequirementOut)
def get_requirement(
    requirement_id: UUID, scope: ReadScope, services: ModelOpsServicesDep
) -> DataRequirementOut:
    return DataRequirementOut.from_domain(services.requirements.get(scope, requirement_id))


@router.post("/{requirement_id}/actions/{action}", response_model=DataRequirementOut)
def act_on_requirement(
    requirement_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    payload: RequirementActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> DataRequirementOut:
    if (
        action in {"approve", "close"}
        and not context.is_platform_admin
        and context.role is not ProjectRole.OWNER
    ):
        raise ForbiddenError("该动作要求项目负责人权限")
    requirement = services.requirements.apply_action(
        scope,
        requirement_id,
        action,
        expected_version=expected_version,
        payload=payload.model_dump(exclude_unset=True) if payload else {},
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return DataRequirementOut.from_domain(requirement)


__all__ = ["router"]
