"""模型评测 HTTP 路由。"""

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
from app.api.v1.modelops_response_models import EvaluationOut
from app.api.v1.modelops_schemas import (
    ActionRequest,
    EvaluationCreate,
)
from app.api.v1.schemas import PageOut
from app.application.modelops.commands import NewEvaluation

router = APIRouter(prefix="/projects/{project_id}/evaluations", tags=["evaluations"])


@router.get("", response_model=PageOut[EvaluationOut])
def list_evaluations(
    scope: ReadScope,
    services: ModelOpsServicesDep,
    model_version_id: UUID | None = None,
    dataset_version_id: UUID | None = None,
    status: str | None = None,
    verdict: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[EvaluationOut]:
    items = services.evaluations.list(
        scope,
        model_version_id=model_version_id,
        dataset_version_id=dataset_version_id,
        status=status,
        verdict=verdict,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.evaluation_id)
    return PageOut(
        items=[EvaluationOut.from_domain(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("", response_model=EvaluationOut, status_code=202)
def create_evaluation(
    payload: EvaluationCreate,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> EvaluationOut:
    evaluation = services.evaluations.create(
        scope,
        NewEvaluation(
            model_version_id=payload.model_version_id,
            dataset_version_id=payload.dataset_version_id,
            metrics=payload.metrics,
            thresholds=payload.thresholds,
            baseline_evaluation_id=payload.baseline_evaluation_id,
            cross_dataset_comparison=payload.cross_dataset_comparison,
            adapter_id=payload.adapter_id,
            config=dict(payload.config),
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return EvaluationOut.from_domain(evaluation)


@router.get("/{evaluation_id}", response_model=EvaluationOut)
def get_evaluation(
    evaluation_id: UUID, scope: ReadScope, services: ModelOpsServicesDep
) -> EvaluationOut:
    return EvaluationOut.from_domain(services.evaluations.get(scope, evaluation_id))


@router.post("/{evaluation_id}/actions/{action}", response_model=EvaluationOut)
def act_on_evaluation(
    evaluation_id: UUID,
    action: str,
    payload: ActionRequest | None,
    request: Request,
    scope: WriteScope,
    services: ModelOpsServicesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
) -> EvaluationOut:
    evaluation = services.evaluations.apply_action(
        scope,
        evaluation_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return EvaluationOut.from_domain(evaluation)


__all__ = ["router"]
