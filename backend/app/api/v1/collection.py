"""采集任务/批次 HTTP 路由。

路由只做协议适配：解析路径/请求体/If-Match/Idempotency-Key，调用应用用例，
再把领域实体序列化为契约响应。权限与项目隔离由服务端依赖强制执行。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from app.api.deps import get_project_scope, get_write_scope
from app.api.v1.collection_dependencies import CollectionUseCasesDep
from app.api.v1.collection_schemas import (
    CollectionBatchActionRequest,
    CollectionBatchCreate,
    CollectionBatchOut,
    CollectionBatchPatch,
    CollectionTaskActionRequest,
    CollectionTaskCreate,
    CollectionTaskOut,
    CollectionTaskPatch,
)
from app.api.v1.preconditions import require_expected_version
from app.api.v1.schemas import PageOut
from app.application.collection import NewCollectionBatch, NewCollectionTask
from app.domain.identity.models import ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["collection"])

ReadScope = Annotated[ProjectScope, Depends(get_project_scope)]
WriteScope = Annotated[ProjectScope, Depends(get_write_scope)]
ExpectedVersion = Annotated[int, Depends(require_expected_version)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=128),
]


@router.get("/collection-tasks", response_model=PageOut[CollectionTaskOut])
def list_collection_tasks(
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
    status: str | None = None,
    assigned_to: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[CollectionTaskOut]:
    page = use_cases.tasks.list_tasks(
        scope,
        status=status,
        assigned_to=assigned_to,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[CollectionTaskOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/collection-tasks", response_model=CollectionTaskOut, status_code=201)
def create_collection_task(
    payload: CollectionTaskCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> CollectionTaskOut:
    task = use_cases.tasks.create_task(
        scope,
        NewCollectionTask(**payload.model_dump()),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionTaskOut.from_domain(task)


@router.get("/collection-tasks/{task_id}", response_model=CollectionTaskOut)
def get_collection_task(
    task_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
) -> CollectionTaskOut:
    return CollectionTaskOut.from_domain(use_cases.tasks.get_task(scope, task_id))


@router.patch("/collection-tasks/{task_id}", response_model=CollectionTaskOut)
def update_collection_task(
    task_id: UUID,
    payload: CollectionTaskPatch,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
) -> CollectionTaskOut:
    task = use_cases.tasks.update_task(
        scope,
        task_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=_request_id(request),
    )
    return CollectionTaskOut.from_domain(task)


@router.post(
    "/collection-tasks/{task_id}/actions/{action}",
    response_model=CollectionTaskOut,
)
def act_on_collection_task(
    task_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: CollectionTaskActionRequest | None = None,
) -> CollectionTaskOut:
    task = use_cases.tasks.apply_action(
        scope,
        task_id,
        action,
        expected_version=expected_version,
        payload=_payload(payload),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionTaskOut.from_domain(task)


@router.get(
    "/collection-tasks/{task_id}/batches",
    response_model=PageOut[CollectionBatchOut],
)
def list_collection_batches(
    task_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
    status: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[CollectionBatchOut]:
    page = use_cases.batches.list_batches(
        scope,
        task_id,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return PageOut(
        items=[CollectionBatchOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/collection-tasks/{task_id}/batches",
    response_model=CollectionBatchOut,
    status_code=201,
)
def create_collection_batch(
    task_id: UUID,
    payload: CollectionBatchCreate,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> CollectionBatchOut:
    batch = use_cases.batches.create_batch(
        scope,
        task_id,
        NewCollectionBatch(**payload.model_dump()),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionBatchOut.from_domain(batch)


@router.get("/collection-batches/{batch_id}", response_model=CollectionBatchOut)
def get_collection_batch(
    batch_id: UUID,
    scope: ReadScope,
    use_cases: CollectionUseCasesDep,
) -> CollectionBatchOut:
    return CollectionBatchOut.from_domain(use_cases.batches.get_batch(scope, batch_id))


@router.patch("/collection-batches/{batch_id}", response_model=CollectionBatchOut)
def update_collection_batch(
    batch_id: UUID,
    payload: CollectionBatchPatch,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
) -> CollectionBatchOut:
    batch = use_cases.batches.update_batch(
        scope,
        batch_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=_request_id(request),
    )
    return CollectionBatchOut.from_domain(batch)


@router.post(
    "/collection-batches/{batch_id}/actions/{action}",
    response_model=CollectionBatchOut,
)
def act_on_collection_batch(
    batch_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    use_cases: CollectionUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: CollectionBatchActionRequest | None = None,
) -> CollectionBatchOut:
    batch = use_cases.batches.apply_action(
        scope,
        batch_id,
        action,
        expected_version=expected_version,
        payload=_payload(payload),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=_request_id(request),
    )
    return CollectionBatchOut.from_domain(batch)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _payload(payload: object | None) -> dict[str, object]:
    if payload is None:
        return {}
    return payload.model_dump(exclude_unset=True)  # type: ignore[attr-defined]
