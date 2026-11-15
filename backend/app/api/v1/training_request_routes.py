"""TrainingRequest / Revision HTTP 路由。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import DbSession, get_project_context
from app.api.v1.dataset_dependencies import (
    IdempotencyKey,
    OwnerScope,
    ReadScope,
    WriteScope,
)
from app.api.v1.dataset_dependencies import (
    request_id as get_request_id,
)
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.dataset_response_models import (
    TrainingRequestOut,
    TrainingRequestRevisionOut,
)
from app.api.v1.schemas import PageOut
from app.api.v1.training_dependencies import ExpectedVersion, TrainingServicesDep
from app.api.v1.training_schemas import (
    TrainingActionRequest,
    TrainingRequestCreate,
    TrainingRequestRevisionCreate,
    TrainingRequestRevisionPatch,
    training_request_out,
    training_revision_out,
)
from app.application.datasets.training_commands import (
    NewTrainingRequest,
    NewTrainingRequestRevision,
)
from app.core.errors import ForbiddenError, ValidationFailedError
from app.domain.datasets.training_enums import TrainingPriority, TrainingRequestStatus
from app.domain.datasets.training_state_machines import allowed_request_actions
from app.domain.identity.models import ProjectContext, ProjectRole
from app.infrastructure.repositories.identity import SqlProjectMembershipRepository

router = APIRouter(prefix="/projects/{project_id}/training-requests", tags=["training-requests"])

_APPROVAL_ACTIONS = frozenset({"approve", "reject"})
_USER_ACTIONS = frozenset({"block", "unblock", "cancel", "close"})


def _status_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return TrainingRequestStatus(value).value
    except ValueError as exc:
        raise ValidationFailedError("status 取值非法") from exc


def _priority_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return TrainingPriority(value).value
    except ValueError as exc:
        raise ValidationFailedError("priority 取值非法") from exc


def _require_owner(context: ProjectContext) -> None:
    if not context.is_platform_admin and context.role is not ProjectRole.OWNER:
        raise ForbiddenError("该动作要求项目负责人（PM/PO）权限")


@router.get("", response_model=PageOut[TrainingRequestOut])
def list_training_requests(
    scope: ReadScope,
    services: TrainingServicesDep,
    status: str | None = None,
    priority: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.requests.list(
        scope,
        status=_status_filter(status),
        priority=_priority_filter(priority),
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.training_request_id)
    return {
        "items": [
            training_request_out(item, allowed_actions=allowed_request_actions(item.status))
            for item in page.items
        ],
        "next_cursor": page.next_cursor,
    }


@router.post("", response_model=TrainingRequestOut, status_code=201)
def create_training_request(
    payload: TrainingRequestCreate,
    request: Request,
    session: DbSession,
    scope: WriteScope,
    services: TrainingServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    _assert_owner_member(session, scope.project_id, payload.owner_id)
    item = services.requests.create(
        scope,
        NewTrainingRequest(
            code=payload.code,
            title=payload.title,
            owner_id=payload.owner_id,
            priority=payload.priority,
            snapshot=payload.snapshot.model_dump(),
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=get_request_id(request),
    )
    return training_request_out(item, allowed_actions=item.allowed_actions)


@router.get("/{request_id}", response_model=TrainingRequestOut)
def get_training_request(
    request_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
) -> dict[str, Any]:
    item = services.requests.get(scope, request_id)
    return training_request_out(item, allowed_actions=item.allowed_actions)


@router.post("/{request_id}/actions/{action}", response_model=TrainingRequestOut)
def act_on_training_request(
    request_id: UUID,
    action: str,
    request: Request,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    scope: OwnerScope,
    services: TrainingServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: TrainingActionRequest | None = None,
) -> dict[str, Any]:
    if action not in _USER_ACTIONS:
        raise ValidationFailedError("训练需求动作非法")
    item = services.requests.apply_request_action(
        scope,
        request_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        restore_status=payload.restore_status if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id_text=get_request_id(request),
    )
    return training_request_out(item, allowed_actions=item.allowed_actions)


@router.get("/{request_id}/revisions", response_model=PageOut[TrainingRequestRevisionOut])
def list_revisions(
    request_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.requests.list_revisions(
        scope,
        request_id,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.revision_id)
    return {
        "items": [training_revision_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post(
    "/{request_id}/revisions",
    response_model=TrainingRequestRevisionOut,
    status_code=201,
)
def create_revision(
    request_id: UUID,
    payload: TrainingRequestRevisionCreate,
    request: Request,
    scope: WriteScope,
    services: TrainingServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    revision = services.requests.create_revision(
        scope,
        request_id,
        NewTrainingRequestRevision(
            snapshot=payload.snapshot.model_dump(),
            change_reason=payload.change_reason,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id_text=get_request_id(request),
    )
    return training_revision_out(revision)


@router.get("/{request_id}/revisions/{revision_id}", response_model=TrainingRequestRevisionOut)
def get_revision(
    request_id: UUID,
    revision_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
) -> dict[str, Any]:
    return training_revision_out(services.requests.get_revision(scope, request_id, revision_id))


@router.patch("/{request_id}/revisions/{revision_id}", response_model=TrainingRequestRevisionOut)
def patch_revision(
    request_id: UUID,
    revision_id: UUID,
    payload: TrainingRequestRevisionPatch,
    request: Request,
    scope: WriteScope,
    services: TrainingServicesDep,
    expected_version: ExpectedVersion,
) -> dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True)
    revision = services.requests.update_revision(
        scope,
        request_id,
        revision_id,
        expected_version=expected_version,
        changes=changes,
        request_id_text=get_request_id(request),
    )
    return training_revision_out(revision)


@router.post(
    "/{request_id}/revisions/{revision_id}/actions/{action}",
    response_model=TrainingRequestRevisionOut,
)
def act_on_revision(
    request_id: UUID,
    revision_id: UUID,
    action: str,
    request: Request,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    scope: WriteScope,
    services: TrainingServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: TrainingActionRequest | None = None,
) -> dict[str, Any]:
    if action not in {"submit", "approve", "reject"}:
        raise ValidationFailedError("需求修订动作非法")
    if action in _APPROVAL_ACTIONS:
        _require_owner(context)
    revision = services.requests.apply_revision_action(
        scope,
        request_id,
        revision_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id_text=get_request_id(request),
    )
    return training_revision_out(revision)


# 供路由层校验 owner 成员资格的小工具（保持导入位置清晰）。
def _assert_owner_member(session, project_id: UUID, owner_id: UUID) -> None:
    role = SqlProjectMembershipRepository(session).role_for(project_id, owner_id)
    if role is None:
        raise ValidationFailedError("owner_id 必须是项目成员")
