"""模型反馈与数据缺口 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated
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
from app.api.v1.modelops_response_models import (
    DataGapSummaryOut,
    FeedbackOut,
)
from app.api.v1.modelops_schemas import (
    FeedbackActionRequest,
    FeedbackCreate,
)
from app.api.v1.schemas import PageOut
from app.application.modelops.commands import NewFeedback

router = APIRouter(prefix="/projects/{project_id}", tags=["model-feedback"])


@router.get("/model-feedback", response_model=PageOut[FeedbackOut])
def list_feedback(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    status: str | None = None,
    issue_type: str | None = None,
    model_version_id: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[FeedbackOut]:
    items = services.feedback.list(
        scope,
        status=status,
        issue_type=issue_type,
        model_version_id=model_version_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.feedback_id)
    return PageOut(
        items=[FeedbackOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/model-feedback", response_model=FeedbackOut, status_code=201)
def create_feedback(
    payload: FeedbackCreate,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> FeedbackOut:
    item = services.feedback.create(
        scope,
        NewFeedback(
            model_version_id=payload.model_version_id,
            issue_type=payload.issue_type,
            severity=payload.severity,
            description=payload.description,
            dimensions=[dimension.to_domain() for dimension in payload.dimensions],
            evaluation_id=payload.evaluation_id,
            inference_task_id=payload.inference_task_id,
            evidence_refs=payload.evidence_refs,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return FeedbackOut.from_domain(item)


@router.get("/model-feedback/{feedback_id}", response_model=FeedbackOut)
def get_feedback(feedback_id: UUID, scope: ReadScope, services: ModelOpsServicesDep) -> FeedbackOut:
    return FeedbackOut.from_domain(services.feedback.get(scope, feedback_id))


@router.post("/model-feedback/{feedback_id}/actions/{action}", response_model=FeedbackOut)
def act_on_feedback(
    feedback_id: UUID,
    action: str,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
    payload: FeedbackActionRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> FeedbackOut:
    item = services.feedback.apply_action(
        scope,
        feedback_id,
        action,
        expected_version=expected_version,
        payload=payload.model_dump(exclude_unset=True) if payload else {},
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return FeedbackOut.from_domain(item)


@router.get("/data-gaps", response_model=DataGapSummaryOut)
def get_gaps(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    dimension_type: str | None = None,
) -> DataGapSummaryOut:
    return DataGapSummaryOut.from_domain(
        services.feedback.gap_summary(scope, dimension_type=dimension_type)
    )


__all__ = ["router"]
