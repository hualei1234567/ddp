"""TrainingDelivery 与外部训练引用 HTTP 路由。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_project_context
from app.api.v1.dataset_dependencies import (
    IdempotencyKey,
    ReadScope,
    WriteScope,
    request_id,
)
from app.api.v1.dataset_pagination import build_page, decode_cursor, fetch_limit
from app.api.v1.dataset_response_models import (
    ExternalTrainingReferenceOut,
    TrainingDeliveryOut,
    TrainingRunEvidenceListOut,
    TrainingRunEvidenceOut,
)
from app.api.v1.schemas import PageOut
from app.api.v1.training_dependencies import (
    ExpectedVersion,
    TrainingServices,
    TrainingServicesDep,
)
from app.api.v1.training_schemas import (
    ExternalTrainingReferenceCreate,
    TrainingActionRequest,
    TrainingDeliveryCreate,
    TrainingRunEvidenceCreate,
    external_reference_out,
    training_delivery_out,
    training_evidence_out,
)
from app.application.datasets.training_commands import (
    NewExternalTrainingReference,
    NewTrainingDelivery,
)
from app.application.datasets.training_evidence import NewTrainingRunEvidence
from app.core.errors import ForbiddenError, ValidationFailedError
from app.domain.datasets.training_deliveries import TrainingDelivery
from app.domain.datasets.training_enums import ExecutionMode, TrainingDeliveryStatus
from app.domain.identity.models import ProjectContext, ProjectRole, ProjectScope

router = APIRouter(prefix="/projects/{project_id}", tags=["training-deliveries"])


def _delivery_out(
    delivery: TrainingDelivery,
    modes: dict[UUID, ExecutionMode],
) -> dict[str, Any]:
    return training_delivery_out(
        delivery,
        execution_mode=modes.get(delivery.training_delivery_id, ExecutionMode.UNKNOWN),
    )


def _delivery_modes(
    scope: ProjectScope,
    services: TrainingServices,
    deliveries: list[TrainingDelivery],
) -> dict[UUID, ExecutionMode]:
    return services.references.latest_execution_modes(
        scope, [delivery.training_delivery_id for delivery in deliveries]
    )


def _status_filter(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return TrainingDeliveryStatus(value).value
    except ValueError as exc:
        raise ValidationFailedError("status 取值非法") from exc


@router.get("/training-deliveries", response_model=PageOut[TrainingDeliveryOut])
def list_deliveries(
    scope: ReadScope,
    services: TrainingServicesDep,
    training_request_id: UUID | None = None,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.deliveries.list(
        scope,
        request_id=training_request_id,
        status=_status_filter(status),
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.training_delivery_id)
    modes = _delivery_modes(scope, services, page.items)
    return {
        "items": [_delivery_out(item, modes) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post("/training-deliveries", response_model=TrainingDeliveryOut, status_code=201)
def create_delivery(
    payload: TrainingDeliveryCreate,
    request: Request,
    scope: WriteScope,
    services: TrainingServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    delivery = services.deliveries.create(
        scope,
        NewTrainingDelivery(
            training_request_id=payload.training_request_id,
            training_request_revision_id=payload.training_request_revision_id,
            dataset_version_id=payload.dataset_version_id,
            export_job_id=payload.export_job_id,
            recipient_id=payload.recipient_id,
            recipient_team=payload.recipient_team,
            delivery_note_no=payload.delivery_note_no,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return _delivery_out(delivery, _delivery_modes(scope, services, [delivery]))


@router.get("/training-deliveries/{delivery_id}", response_model=TrainingDeliveryOut)
def get_delivery(
    delivery_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
) -> dict[str, Any]:
    delivery = services.deliveries.get(scope, delivery_id)
    return _delivery_out(delivery, _delivery_modes(scope, services, [delivery]))


@router.post(
    "/training-deliveries/{delivery_id}/actions/{action}",
    response_model=TrainingDeliveryOut,
)
def act_on_delivery(
    delivery_id: UUID,
    action: str,
    request: Request,
    context: Annotated[ProjectContext, Depends(get_project_context)],
    services: TrainingServicesDep,
    expected_version: ExpectedVersion,
    scope: WriteScope,
    idempotency_key: IdempotencyKey = None,
    payload: TrainingActionRequest | None = None,
) -> dict[str, Any]:
    if action not in {
        "ready",
        "deliver",
        "accept",
        "reject",
        "rework",
        "resume-ready",
        "cancel",
    }:
        raise ValidationFailedError("交付动作非法")
    if (
        action == "cancel"
        and not context.is_platform_admin
        and context.role is not ProjectRole.OWNER
    ):
        raise ForbiddenError("取消交付要求项目负责人权限")
    delivery = services.deliveries.apply_action(
        scope,
        delivery_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        opinion=payload.acceptance_opinion if payload is not None else None,
        rework_ref=payload.rework_ref if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return _delivery_out(delivery, _delivery_modes(scope, services, [delivery]))


@router.get(
    "/training-deliveries/{delivery_id}/external-references",
    response_model=PageOut[ExternalTrainingReferenceOut],
)
def list_external_references(
    delivery_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    items = services.references.list_for_delivery(
        scope,
        delivery_id,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.external_ref_id)
    return {
        "items": [external_reference_out(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@router.post(
    "/training-deliveries/{delivery_id}/external-references",
    response_model=ExternalTrainingReferenceOut,
    status_code=202,
)
def create_external_reference(
    delivery_id: UUID,
    payload: ExternalTrainingReferenceCreate,
    request: Request,
    scope: WriteScope,
    services: TrainingServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    reference = services.references.create(
        scope,
        delivery_id,
        NewExternalTrainingReference(
            adapter_id=payload.adapter_id,
            adapter_version=payload.adapter_version,
            correlation_id=payload.correlation_id,
            model_ref=payload.model_ref,
            evaluation_ref=payload.evaluation_ref,
            config=payload.config,
            code_version=payload.code_version,
            model_version=payload.model_version,
            execution_mode=payload.execution_mode,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return external_reference_out(reference)


@router.get(
    "/external-training-references/{external_ref_id}",
    response_model=ExternalTrainingReferenceOut,
)
def get_external_reference(
    external_ref_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
) -> dict[str, Any]:
    return external_reference_out(services.references.get(scope, external_ref_id))


@router.post(
    "/external-training-references/{external_ref_id}/actions/{action}",
    response_model=ExternalTrainingReferenceOut,
)
def act_on_external_reference(
    external_ref_id: UUID,
    action: str,
    request: Request,
    services: TrainingServicesDep,
    expected_version: ExpectedVersion,
    scope: WriteScope,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    if action not in {"sync", "cancel"}:
        raise ValidationFailedError("外部训练引用动作非法")
    reference = services.references.apply_action(
        scope,
        external_ref_id,
        action,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return external_reference_out(reference)


@router.get(
    "/training-deliveries/{delivery_id}/evidence",
    response_model=TrainingRunEvidenceListOut,
)
def list_delivery_training_evidence(
    delivery_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    items = services.evidence.list_for_delivery(
        scope,
        delivery_id,
        limit=limit,
        offset=offset,
    )
    return {"items": [training_evidence_out(item) for item in items]}


@router.get(
    "/external-training-references/{external_ref_id}/evidence",
    response_model=TrainingRunEvidenceListOut,
)
def list_external_reference_evidence(
    external_ref_id: UUID,
    scope: ReadScope,
    services: TrainingServicesDep,
) -> dict[str, Any]:
    items = services.evidence.list_for_reference(scope, external_ref_id)
    return {"items": [training_evidence_out(item) for item in items]}


@router.post(
    "/external-training-references/{external_ref_id}/evidence",
    response_model=TrainingRunEvidenceOut,
    status_code=201,
)
def create_external_reference_evidence(
    external_ref_id: UUID,
    payload: TrainingRunEvidenceCreate,
    scope: WriteScope,
    services: TrainingServicesDep,
) -> dict[str, Any]:
    evidence = services.evidence.record(
        scope,
        external_ref_id,
        NewTrainingRunEvidence(
            dataset_version_id=payload.dataset_version_id,
            run_kind=payload.run_kind,
            device=payload.device,
            runtime_versions=payload.runtime_versions,
            config_sha256=payload.config_sha256,
            seed=payload.seed,
            batch_size=payload.batch_size,
            steps=payload.steps,
            started_at=payload.started_at,
            finished_at=payload.finished_at,
            metrics_summary=payload.metrics_summary,
            checkpoint_ref=payload.checkpoint_ref,
            checkpoint_sha256=payload.checkpoint_sha256,
            exit_code=payload.exit_code,
            stdout_tail=payload.stdout_tail,
        ),
    )
    return training_evidence_out(evidence)


__all__ = ["router"]
