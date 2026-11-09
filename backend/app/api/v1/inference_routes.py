"""推理服务与推理任务 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.modelops_dependencies import (
    ExpectedVersion,
    IdempotencyKey,
    ModelOpsServicesDep,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.modelops_schemas import (
    ActionRequest,
    InferenceServiceCreate,
    InferenceTaskCreate,
    service_out,
    task_out,
)
from app.application.modelops.commands import NewInferenceService, NewInferenceTask

router = APIRouter(prefix="/projects/{project_id}", tags=["inference"])


@router.get("/inference-services")
def list_services(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.services.list(
        scope, status=status, after_id=decode_cursor(cursor), limit=fetch_limit(limit)
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.service_id)
    return {
        "items": [service_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("/inference-services", status_code=201)
def create_service(
    payload: InferenceServiceCreate,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    service = services.services.create(
        scope,
        NewInferenceService(
            model_version_id=payload.model_version_id,
            environment=payload.environment,
            endpoint_url=payload.endpoint_url,
            resource_ref=payload.resource_ref,
            config=payload.config,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return service_out(service)


@router.get("/inference-services/{service_id}")
def get_service(
    service_id: UUID, scope: ReadScope, services: ModelOpsServicesDep
) -> dict[str, Any]:
    return service_out(services.services.get(scope, service_id))


@router.post("/inference-services/{service_id}/actions/{action}")
def act_on_service(
    service_id: UUID,
    action: str,
    payload: ActionRequest | None,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    service = services.services.apply_action(
        scope,
        service_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return service_out(service)


@router.get("/inference-tasks")
def list_tasks(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    status: str | None = None,
    model_version_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.tasks.list(
        scope,
        status=status,
        model_version_id=model_version_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.task_id)
    return {
        "items": [task_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("/inference-tasks", status_code=202)
def create_task(
    payload: InferenceTaskCreate,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    task = services.tasks.create(
        scope,
        NewInferenceTask(
            model_version_id=payload.model_version_id,
            service_id=payload.service_id,
            dataset_version_id=payload.dataset_version_id,
            episode_ids=payload.episode_ids,
            adapter_id=payload.adapter_id,
            config=payload.config,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return task_out(task)


@router.get("/inference-tasks/{task_id}")
def get_task(task_id: UUID, scope: ReadScope, services: ModelOpsServicesDep) -> dict[str, Any]:
    return task_out(services.tasks.get(scope, task_id))


@router.post("/inference-tasks/{task_id}/actions/{action}")
def act_on_task(
    task_id: UUID,
    action: str,
    payload: ActionRequest | None,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    task = services.tasks.apply_action(
        scope,
        task_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return task_out(task)


__all__ = ["router"]
