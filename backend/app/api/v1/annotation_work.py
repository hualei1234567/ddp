"""标注任务、批次与分配 HTTP 路由。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_context
from app.api.v1.annotation_common import (
    ActionName,
    ExpectedVersion,
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.annotation_consistency_models import DoubleAnnotationConsistencyOut
from app.api.v1.annotation_dependencies import AnnotationUseCasesDep
from app.api.v1.annotation_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.annotation_work_models import (
    AnnotationAssignmentActionRequest,
    AnnotationAssignmentCapacityPreviewOut,
    AnnotationAssignmentCapacityPreviewRequest,
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
    AnnotationWorkloadOut,
)
from app.api.v1.schemas import PageOut
from app.application.annotation.commands import (
    NewAnnotationAssignment,
    NewAnnotationBatch,
    NewAnnotationTask,
)
from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError
from app.domain.annotation.workload import CapacityPolicy
from app.domain.identity.models import ProjectContext, ProjectRole

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-tasks"])


def _assert_auto_approve_allowed(
    iaa_policy: Mapping[str, object] | None,
    context: ProjectContext,
) -> None:
    """自动通过会以 OWNER 身份留下"人工审批"记录，开启权限必须同级。

    创建与修改都必须走这道闸口：只在创建时校验，EDITOR 可以先用普通策略建
    DRAFT 任务、再 PATCH 打开 auto_approve。
    """
    if not iaa_policy or not iaa_policy.get("auto_approve"):
        return
    if context.is_platform_admin or context.role is ProjectRole.OWNER:
        return
    raise ForbiddenError("只有项目负责人可以开启双标自动通过")


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
    context: Annotated[ProjectContext, Depends(get_project_context)],
    idempotency_key: IdempotencyKey = None,
) -> AnnotationTaskOut:
    policy = payload.merged_iaa_policy()
    _assert_auto_approve_allowed(policy, context)
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
            iaa_policy=policy,
            quality_policy=payload.quality_policy,
            required_skills=tuple(
                {
                    "tag_name": t.tag_name,
                    "category": t.category,
                    "weight": t.weight,
                    "required": t.required,
                }
                for t in payload.required_skills
            ),
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


@router.get(
    "/annotation-tasks/{task_id}/consistency",
    response_model=DoubleAnnotationConsistencyOut,
)
def get_task_consistency(
    task_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> DoubleAnnotationConsistencyOut:
    """双标一致性结论（只读，不创建仲裁、不自动通过）。"""
    return DoubleAnnotationConsistencyOut.from_result(
        use_cases.consistency.evaluate(scope, task_id)
    )


@router.patch("/annotation-tasks/{task_id}", response_model=AnnotationTaskOut)
def update_task(
    task_id: UUID,
    payload: AnnotationTaskPatch,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    expected_version: ExpectedVersion,
) -> AnnotationTaskOut:
    changes = payload.model_dump(exclude_unset=True)
    if "iaa_policy" in changes:
        _assert_auto_approve_allowed(payload.iaa_policy, context)
    task = use_cases.tasks.update(
        scope,
        task_id,
        expected_version=expected_version,
        changes=changes,
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


@router.get(
    "/annotation-workload",
    response_model=AnnotationWorkloadOut,
)
def get_workload(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    settings: Annotated[Settings, Depends(get_settings)],
    assignee_id: UUID,
    period_start: datetime,
    period_end: datetime,
) -> AnnotationWorkloadOut:
    """周期工作量聚合（只读）。

    周期必填：没有时间窗口，「工作量」没有定义，默认成"全部时间"会让
    「本期超容量」变成「历史总量超容量」。容量来自部署配置；未配置时
    如实返回 `capacity_minutes=null`，不把缺失当成 0。
    """
    summary = use_cases.assignments.workload(
        scope,
        assignee_id=assignee_id,
        period_start=period_start,
        period_end=period_end,
        capacity=CapacityPolicy(capacity_minutes=settings.annotation_capacity_minutes),
    )
    return AnnotationWorkloadOut.from_domain(summary)


@router.post(
    "/annotation-assignment-capacity-preview",
    response_model=list[AnnotationAssignmentCapacityPreviewOut],
)
def preview_assignment_capacity(
    payload: AnnotationAssignmentCapacityPreviewRequest,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[AnnotationAssignmentCapacityPreviewOut]:
    """分配前实时负载预览（只读）。

    为什么是 POST 而不是 GET：要一次问多人，UUID 列表放 query string
    会撞长度与编码边界；但**它没有任何副作用**——用 ``ReadScope`` 而非
    ``WriteScope`` 就是这条断言的机器可验证形式：viewer 也能预览，
    预览本身不写库、不落审计、不推进版本、不需要幂等键。

    判定与硬阻断同源（:meth:`CapacityPolicy.exceeded`）：这里返回的
    ``will_exceed`` 就是"提交时会不会被 409 拒绝"的答案。前端据此给红/黄
    警示，但**不自行阻断**——是否放行仍由 ``POST /annotation-assignments``
    的容量策略决定，避免两处各自维护一套阻断规则。
    """
    previews = use_cases.assignments.preview_assignment_capacity(
        scope,
        assignee_ids=tuple(payload.assignee_ids),
        planned_item_count=payload.planned_item_count,
        planned_minutes=payload.planned_minutes,
        capacity=CapacityPolicy(
            max_open_items=settings.annotation_max_open_items,
            max_planned_minutes=settings.annotation_max_planned_minutes,
        ),
    )
    return [AnnotationAssignmentCapacityPreviewOut.from_domain(item) for item in previews]


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
