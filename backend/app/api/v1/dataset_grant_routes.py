"""DatasetGrant HTTP 路由：跨项目授权创建、审批、驳回与撤销。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.v1.dataset_dependencies import (
    IdempotencyKey,
    OwnerScope,
    ReadScope,
    request_id,
)
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.training_dependencies import ExpectedVersion, TrainingServicesDep
from app.api.v1.training_schemas import (
    DatasetGrantCreate,
    TrainingActionRequest,
    grant_out,
)
from app.application.datasets.training_commands import NewDatasetGrant
from app.core.errors import ValidationFailedError
from app.domain.datasets.training_enums import DatasetGrantStatus

router = APIRouter(
    prefix="/projects/{project_id}/datasets/{dataset_id}/grants",
    tags=["dataset-grants"],
)


def _status_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return DatasetGrantStatus(value).value
    except ValueError as exc:
        raise ValidationFailedError("status 取值非法") from exc


@router.get("")
def list_grants(
    dataset_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
    status: str | None = None,
    grantee_project_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.grants.list(
        scope,
        dataset_id,
        status=_status_filter(status),
        grantee_project_id=grantee_project_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.grant_id)
    return {
        "items": [grant_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("", status_code=201)
def create_grant(
    dataset_id: UUID,
    payload: DatasetGrantCreate,
    request: Request,
    scope: OwnerScope,
    services: TrainingServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    grant = services.grants.create(
        scope,
        dataset_id,
        NewDatasetGrant(
            dataset_version_id=payload.dataset_version_id,
            grantee_project_id=payload.grantee_project_id,
            grantee_team=payload.grantee_team,
            scope=tuple(payload.scope),
            reason=payload.reason,
            expires_at=payload.expires_at,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return grant_out(grant)


@router.get("/{grant_id}")
def get_grant(
    dataset_id: UUID,
    grant_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
) -> dict[str, Any]:
    return grant_out(services.grants.get(scope, dataset_id, grant_id))


@router.post("/{grant_id}/actions/{action}")
def act_on_grant(
    dataset_id: UUID,
    grant_id: UUID,
    action: str,
    request: Request,
    scope: OwnerScope,
    services: TrainingServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: TrainingActionRequest | None = None,
) -> dict[str, Any]:
    if action not in {"approve", "reject", "revoke"}:
        raise ValidationFailedError("授权动作非法")
    grant = services.grants.apply_action(
        scope,
        dataset_id,
        grant_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return grant_out(grant)


__all__ = ["router"]
