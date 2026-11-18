"""模型与模型版本 HTTP 路由。"""

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
from app.api.v1.modelops_response_models import ModelOut, ModelVersionOut
from app.api.v1.modelops_schemas import (
    ActionRequest,
    ModelCreate,
    ModelPatch,
    ModelVersionCreate,
)
from app.api.v1.schemas import PageOut
from app.application.modelops.commands import NewModel, NewModelVersion
from app.core.errors import ForbiddenError, ValidationFailedError
from app.domain.identity.models import ProjectContext, ProjectRole
from app.domain.modelops.artifacts import ModelArtifact, ModelRuntime

router = APIRouter(prefix="/projects/{project_id}/models", tags=["models"])


def _require_owner(context: ProjectContext) -> None:
    if not context.is_platform_admin and context.role is not ProjectRole.OWNER:
        raise ForbiddenError("该动作要求项目负责人权限")


@router.get("", response_model=PageOut[ModelOut])
def list_models(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    status: str | None = None,
    task_type: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[ModelOut]:
    items = services.models.list(
        scope,
        status=status,
        task_type=task_type,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.model_id)
    return PageOut(
        items=[ModelOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=ModelOut, status_code=201)
def create_model(
    payload: ModelCreate,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> ModelOut:
    model = services.models.create(
        scope,
        NewModel(
            code=payload.code,
            name=payload.name,
            task_type=payload.task_type,
            framework=payload.framework,
            owner_id=payload.owner_id,
            description=payload.description,
            tags=payload.tags,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return ModelOut.from_domain(model)


@router.get("/{model_id}", response_model=ModelOut)
def get_model(model_id: UUID, scope: ReadScope, services: ModelOpsServicesDep) -> ModelOut:
    return ModelOut.from_domain(services.models.get(scope, model_id))


@router.patch("/{model_id}", response_model=ModelOut)
def update_model(
    model_id: UUID,
    payload: ModelPatch,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
) -> ModelOut:
    model = services.models.update(
        scope,
        model_id,
        payload.model_dump(exclude_unset=True),
        expected_version=expected_version,
        request_id=request_id(request),
    )
    return ModelOut.from_domain(model)


@router.post("/{model_id}/actions/{action}", response_model=ModelOut)
def act_on_model(
    model_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> ModelOut:
    model = services.models.apply_action(
        scope,
        model_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return ModelOut.from_domain(model)


@router.get("/{model_id}/versions", response_model=PageOut[ModelVersionOut])
def list_versions(
    model_id: UUID,
    scope: ReadScope,
    services: ModelOpsServicesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[ModelVersionOut]:
    items = services.versions.list(
        scope, model_id, status=status, after_id=decode_cursor(cursor), limit=fetch_limit(limit)
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.model_version_id)
    return PageOut(
        items=[ModelVersionOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/{model_id}/versions", response_model=ModelVersionOut, status_code=201)
def create_version(
    model_id: UUID,
    payload: ModelVersionCreate,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> ModelVersionOut:
    version = services.versions.create(
        scope,
        model_id,
        NewModelVersion(
            artifact=ModelArtifact(
                object_key=payload.artifact.object_key,
                sha256=payload.artifact.sha256,
                size_bytes=payload.artifact.size_bytes,
                format=payload.artifact.format,
            ),
            runtime=ModelRuntime(
                framework=payload.runtime.framework,
                framework_version=payload.runtime.framework_version,
                python_version=payload.runtime.python_version,
                cuda_version=payload.runtime.cuda_version,
                image_ref=payload.runtime.image_ref,
            ),
            training_request_id=payload.training_request_id,
            training_delivery_id=payload.training_delivery_id,
            external_ref_id=payload.external_ref_id,
            dataset_version_ids=payload.dataset_version_ids,
            metrics_summary=payload.metrics_summary,
            release_notes=payload.release_notes,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return ModelVersionOut.from_domain(version)


@router.get("/{model_id}/versions/{version_id}", response_model=ModelVersionOut)
def get_version(
    model_id: UUID, version_id: UUID, scope: ReadScope, services: ModelOpsServicesDep
) -> ModelVersionOut:
    return ModelVersionOut.from_domain(services.versions.get(scope, model_id, version_id))


@router.post(
    "/{model_id}/versions/{version_id}/actions/{action}",
    response_model=ModelVersionOut,
)
def act_on_version(
    model_id: UUID,
    version_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    payload: ActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> ModelVersionOut:
    if action == "publish":
        _require_owner(context)
    version = services.versions.apply_action(
        scope,
        model_id,
        version_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return ModelVersionOut.from_domain(version)


__all__ = ["router", "ValidationFailedError"]
