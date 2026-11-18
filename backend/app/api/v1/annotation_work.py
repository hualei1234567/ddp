"""标注任务、批次与分配 HTTP 路由。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.v1.annotation_common import (
    ActionName,
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.annotation_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.annotation_work_models import (
    AnnotationAssignmentActionRequest,
    AnnotationAssignmentCreate,
    AnnotationAssignmentOut,
    AnnotationBatchActionRequest,
    AnnotationBatchAssignmentCreate,
    AnnotationBatchCreate,
    AnnotationBatchOut,
    AnnotationBatchPatch,
    AnnotationTaskActionRequest,
    AnnotationTaskCreate,
    AnnotationTaskOut,
    AnnotationTaskPatch,
)
from app.api.v1.schemas import PageOut
from app.application.annotation.commands import (
    NewAnnotationAssignment,
    NewAnnotationBatch,
    NewAnnotationTask,
)
from app.core.config import Settings, get_settings
from app.domain.annotation.workload import CapacityPolicy

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-tasks"])


@router.get("/annotation-tasks", response_model=PageOut[AnnotationTaskOut])
def list_tasks(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    status: str | None = None,
    overdue: bool | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[AnnotationTaskOut]:
    items = use_cases.tasks.list(
        scope,
        status=status,
        overdue=overdue,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.task_id)
    # 同一页用同一个"现在"计算逾期，避免边界时刻前后两行口径不一致。
    moment = datetime.now(UTC)
    return PageOut(
        items=[AnnotationTaskOut.from_domain(item, now=moment) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/annotation-tasks", response_model=AnnotationTaskOut, status_code=201)
def create_task(
    payload: AnnotationTaskCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationTaskOut:
    task = use_cases.tasks.create(
        scope,
        NewAnnotationTask(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            sample_set_id=payload.sample_set_id,
            schema_id=payload.schema_id,
            schema_version=payload.schema_version,
            target_item_count=payload.target_item_count,
            target_episode_count=payload.target_episode_count,
            due_at=payload.due_at,
            priority=payload.priority,
            assignment_mode=payload.assignment_mode,
            iaa_policy=payload.iaa_policy,
            quality_policy=payload.quality_policy,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationTaskOut.from_domain(task)


@router.get("/annotation-tasks/{task_id}", response_model=AnnotationTaskOut)
def get_task(
    task_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationTaskOut:
    return AnnotationTaskOut.from_domain(use_cases.tasks.get(scope, task_id))


@router.patch("/annotation-tasks/{task_id}", response_model=AnnotationTaskOut)
def update_task(
    task_id: UUID,
    payload: AnnotationTaskPatch,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
) -> AnnotationTaskOut:
    task = use_cases.tasks.update(
        scope,
        task_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=request_id(request),
    )
    return AnnotationTaskOut.from_domain(task)


@router.post("/annotation-tasks/{task_id}/actions/{action}", response_model=AnnotationTaskOut)
def act_on_task(
    task_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AnnotationTaskActionRequest | None = None,
) -> AnnotationTaskOut:
    task = use_cases.tasks.apply_action(
        scope,
        task_id,
        action,
        expected_version=expected_version,
        payload=payload.model_dump(exclude_unset=True) if payload is not None else {},
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationTaskOut.from_domain(task)


@router.get(
    "/annotation-tasks/{task_id}/batches",
    response_model=PageOut[AnnotationBatchOut],
)
def list_batches(
    task_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[AnnotationBatchOut]:
    items = use_cases.batches.list(
        scope,
        task_id=task_id,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.batch_id)
    return PageOut(
        items=[AnnotationBatchOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/annotation-tasks/{task_id}/annotation-batches",
    response_model=AnnotationBatchOut,
    status_code=201,
    include_in_schema=False,
)
@router.post(
    "/annotation-tasks/{task_id}/batches",
    response_model=AnnotationBatchOut,
    status_code=201,
)
def create_batch(
    task_id: UUID,
    payload: AnnotationBatchCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> AnnotationBatchOut:
    batch = use_cases.batches.create(
        scope,
        task_id,
        NewAnnotationBatch(
            planned_item_count=payload.planned_item_count,
            code=payload.code,
            sequence_no=payload.sequence_no,
            name=payload.name,
            stratum=payload.stratum,
            item_refs=tuple(payload.item_refs),
            due_at=payload.due_at,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationBatchOut.from_domain(batch)


@router.get("/annotation-batches/{batch_id}", response_model=AnnotationBatchOut)
def get_batch(
    batch_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationBatchOut:
    return AnnotationBatchOut.from_domain(use_cases.batches.get(scope, batch_id))


@router.patch("/annotation-batches/{batch_id}", response_model=AnnotationBatchOut)
def update_batch(
    batch_id: UUID,
    payload: AnnotationBatchPatch,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
) -> AnnotationBatchOut:
    batch = use_cases.batches.update(
        scope,
        batch_id,
        expected_version=expected_version,
        changes=payload.model_dump(exclude_unset=True),
        request_id=request_id(request),
    )
    return AnnotationBatchOut.from_domain(batch)


@router.post(
    "/annotation-batches/{batch_id}/actions/{action}",
    response_model=AnnotationBatchOut,
)
def act_on_batch(
    batch_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AnnotationBatchActionRequest | None = None,
) -> AnnotationBatchOut:
    batch = use_cases.batches.apply_action(
        scope,
        batch_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationBatchOut.from_domain(batch)


@router.get(
    "/annotation-assignments",
    response_model=PageOut[AnnotationAssignmentOut],
)
def list_assignments(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    task_id: UUID | None = None,
    batch_id: UUID | None = None,
    assignee_id: UUID | None = None,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[AnnotationAssignmentOut]:
    items = use_cases.assignments.list(
        scope,
        task_id=task_id,
        batch_id=batch_id,
        assignee_id=assignee_id,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.assignment_id)
    return PageOut(
        items=[AnnotationAssignmentOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/annotation-assignments",
    response_model=AnnotationAssignmentOut,
    status_code=201,
)
def create_assignment(
    payload: AnnotationAssignmentCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    settings: Annotated[Settings, Depends(get_settings)],
    idempotency_key: IdempotencyKey = None,
) -> AnnotationAssignmentOut:
    return _create_assignment(
        scope,
        use_cases,
        payload.batch_id,
        payload.assignee_id,
        payload.assignment_role,
        payload.planned_item_count,
        payload.planned_minutes,
        payload.lease_expires_at,
        request,
        idempotency_key,
        capacity=CapacityPolicy(
            max_open_items=settings.annotation_max_open_items,
            max_planned_minutes=settings.annotation_max_planned_minutes,
        ),
    )


@router.post(
    "/annotation-batches/{batch_id}/annotation-assignments",
    response_model=AnnotationAssignmentOut,
    status_code=201,
    include_in_schema=False,
)
def create_batch_assignment(
    batch_id: UUID,
    payload: AnnotationBatchAssignmentCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    settings: Annotated[Settings, Depends(get_settings)],
    idempotency_key: IdempotencyKey = None,
) -> AnnotationAssignmentOut:
    return _create_assignment(
        scope,
        use_cases,
        batch_id,
        payload.assignee_id,
        payload.assignment_role,
        payload.planned_item_count,
        payload.planned_minutes,
        payload.lease_expires_at,
        request,
        idempotency_key,
        capacity=CapacityPolicy(
            max_open_items=settings.annotation_max_open_items,
            max_planned_minutes=settings.annotation_max_planned_minutes,
        ),
    )


@router.get(
    "/annotation-assignments/{assignment_id}",
    response_model=AnnotationAssignmentOut,
)
def get_assignment(
    assignment_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> AnnotationAssignmentOut:
    return AnnotationAssignmentOut.from_domain(use_cases.assignments.get(scope, assignment_id))


@router.post(
    "/annotation-assignments/{assignment_id}/actions/{action}",
    response_model=AnnotationAssignmentOut,
)
def act_on_assignment(
    assignment_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: AnnotationAssignmentActionRequest | None = None,
) -> AnnotationAssignmentOut:
    assignment = use_cases.assignments.apply_action(
        scope,
        assignment_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        actual_minutes=payload.actual_minutes if payload is not None else None,
        submitted_item_count=payload.submitted_item_count if payload is not None else None,
        approved_item_count=payload.approved_item_count if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationAssignmentOut.from_domain(assignment)


def _create_assignment(
    scope,
    use_cases: AnnotationUseCasesDep,
    batch_id: UUID,
    assignee_id: UUID,
    assignment_role: str,
    planned_item_count: int,
    planned_minutes: int,
    lease_expires_at,
    request: Request,
    idempotency_key: str | None,
    *,
    capacity: CapacityPolicy | None = None,
) -> AnnotationAssignmentOut:
    assignment = use_cases.assignments.create(
        scope,
        batch_id,
        NewAnnotationAssignment(
            assignee_id=assignee_id,
            assignment_role=assignment_role,
            planned_item_count=planned_item_count,
            planned_minutes=planned_minutes,
            lease_expires_at=lease_expires_at,
        ),
        capacity=capacity,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return AnnotationAssignmentOut.from_domain(assignment)
