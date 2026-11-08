"""黄金集、质检抽检与 IAA 报告 HTTP 路由。

黄金集详情与列表始终使用脱敏视图；答案只在授权端点返回并写入审计。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

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
from app.api.v1.annotation_quality_models import (
    GoldEvaluationCreate,
    GoldEvaluationOut,
    GoldInjectionCreate,
    GoldInjectionOut,
    GoldSetActionRequest,
    GoldSetAnswerOut,
    GoldSetCreate,
    GoldSetOut,
    IAAReportCreate,
    IAAReportOut,
    QualitySampleActionRequest,
    QualitySampleCreate,
    QualitySampleOut,
    QualitySamplePlanRequest,
)
from app.api.v1.schemas import PageOut
from app.application.annotation.commands import (
    NewGoldSet,
    NewQualitySample,
    QualityCandidateSpec,
)
from app.core.errors import ValidationFailedError

router = APIRouter(prefix="/projects/{project_id}", tags=["annotation-quality"])


@router.get("/gold-sets", response_model=PageOut[GoldSetOut])
@router.get(
    "/annotation-gold-sets",
    response_model=PageOut[GoldSetOut],
    include_in_schema=False,
)
def list_gold_sets(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    status: str | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[GoldSetOut]:
    items = use_cases.gold_sets.list(
        scope,
        status=status,
        after_id=decode_cursor(cursor),
        limit=fetch_limit(limit),
    )
    page = build_page(items, limit=limit, id_of=lambda item: item.gold_set_id)
    return PageOut(
        items=[GoldSetOut(**item.redacted_view()) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.post("/gold-sets", response_model=GoldSetOut, status_code=201)
@router.post(
    "/annotation-gold-sets",
    response_model=GoldSetOut,
    status_code=201,
    include_in_schema=False,
)
def create_gold_set(
    payload: GoldSetCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> GoldSetOut:
    schema = use_cases.schemas.get(scope, payload.schema_id)
    if payload.schema_version is not None and payload.schema_version != schema.schema_version:
        raise ValidationFailedError("黄金集 Schema 版本与当前已发布版本不一致")
    if payload.schema_sha256 is not None and payload.schema_sha256 != schema.schema_sha256:
        raise ValidationFailedError("黄金集 Schema 快照 Hash 不一致")
    gold_set = use_cases.gold_sets.create(
        scope,
        NewGoldSet(
            code=payload.code,
            name=payload.name,
            schema_id=payload.schema_id,
            items=tuple(payload.items),
            exposure_policy=payload.exposure_policy,
            max_exposure_per_annotator=payload.max_exposure_per_annotator,
            rotation_policy=payload.rotation_policy,
            usage_policy=payload.usage_policy,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return GoldSetOut(**gold_set.redacted_view())


@router.get("/gold-sets/{gold_set_id}", response_model=GoldSetOut)
@router.get(
    "/annotation-gold-sets/{gold_set_id}",
    response_model=GoldSetOut,
    include_in_schema=False,
)
def get_gold_set(
    gold_set_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> GoldSetOut:
    return GoldSetOut(**use_cases.gold_sets.redacted_view(scope, gold_set_id))


@router.get("/gold-sets/{gold_set_id}/answers", response_model=GoldSetAnswerOut)
@router.get(
    "/annotation-gold-sets/{gold_set_id}/answers",
    response_model=GoldSetAnswerOut,
    include_in_schema=False,
)
def get_gold_set_answers(
    gold_set_id: UUID,
    request: Request,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> GoldSetAnswerOut:
    payload = use_cases.gold_sets.answer_view(
        scope,
        gold_set_id,
        request_id=request_id(request),
    )
    return GoldSetAnswerOut(**payload)


@router.post("/gold-sets/{gold_set_id}/actions/{action}", response_model=GoldSetOut)
@router.post(
    "/annotation-gold-sets/{gold_set_id}/actions/{action}",
    response_model=GoldSetOut,
    include_in_schema=False,
)
def act_on_gold_set(
    gold_set_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: GoldSetActionRequest | None = None,
) -> GoldSetOut:
    gold_set = use_cases.gold_sets.apply_action(
        scope,
        gold_set_id,
        action,
        expected_version=expected_version,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return GoldSetOut(**gold_set.redacted_view())


@router.post(
    "/gold-sets/{gold_set_id}/evaluations",
    response_model=GoldEvaluationOut,
    status_code=201,
)
@router.post(
    "/annotation-gold-sets/{gold_set_id}/evaluations",
    response_model=GoldEvaluationOut,
    status_code=201,
    include_in_schema=False,
)
def evaluate_gold_set(
    gold_set_id: UUID,
    payload: GoldEvaluationCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
) -> GoldEvaluationOut:
    record = use_cases.gold_sets.evaluate(
        scope,
        gold_set_id,
        task_id=payload.task_id,
        submission_id=payload.submission_id,
        request_id=request_id(request),
    )
    return GoldEvaluationOut.from_record(record)


@router.post(
    "/gold-sets/{gold_set_id}/injections",
    response_model=GoldInjectionOut,
    status_code=201,
)
@router.post(
    "/annotation-gold-sets/{gold_set_id}/injections",
    response_model=GoldInjectionOut,
    status_code=201,
    include_in_schema=False,
)
def inject_gold_set(
    gold_set_id: UUID,
    payload: GoldInjectionCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
) -> GoldInjectionOut:
    result = use_cases.gold_sets.inject(
        scope,
        gold_set_id,
        task_id=payload.task_id,
        annotator_id=payload.annotator_id,
        item_ids=tuple(payload.item_ids),
        request_id=request_id(request),
    )
    return GoldInjectionOut(**result)


@router.get("/quality-samples", response_model=PageOut[QualitySampleOut])
@router.get(
    "/annotation-quality-samples",
    response_model=PageOut[QualitySampleOut],
    include_in_schema=False,
)
def list_quality_samples(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    task_id: UUID,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[QualitySampleOut]:
    items = use_cases.quality_samples.list_for_task(
        scope,
        task_id,
        status=status,
        limit=limit,
    )
    return PageOut(
        items=[QualitySampleOut.from_domain(item) for item in items],
        next_cursor=None,
    )


@router.post(
    "/quality-samples/plan",
    response_model=list[QualitySampleOut],
    status_code=201,
)
@router.post(
    "/annotation-quality-samples/plan",
    response_model=list[QualitySampleOut],
    status_code=201,
    include_in_schema=False,
)
def plan_quality_samples(
    payload: QualitySamplePlanRequest,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> list[QualitySampleOut]:
    samples = use_cases.quality_samples.plan_for_candidates(
        scope,
        payload.task_id,
        candidate_specs=tuple(
            QualityCandidateSpec(
                submission_id=item.submission_id,
                stratum=item.stratum,
                risk_score=item.risk_score,
            )
            for item in payload.candidates
        ),
        sampling_policy_version=payload.sampling_policy_version,
        sample_type=payload.sample_type,
        sample_size=payload.sample_size,
        sample_reason=payload.sample_reason,
        stratum=payload.stratum,
        risk_threshold=payload.risk_threshold,
        due_at=payload.due_at,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return [QualitySampleOut.from_domain(sample) for sample in samples]


@router.post("/quality-samples", response_model=QualitySampleOut, status_code=201)
@router.post(
    "/annotation-quality-samples",
    response_model=QualitySampleOut,
    status_code=201,
    include_in_schema=False,
)
def create_quality_sample(
    payload: QualitySampleCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> QualitySampleOut:
    sample = use_cases.quality_samples.create_one(
        scope,
        NewQualitySample(
            task_id=payload.task_id,
            submission_id=payload.submission_id,
            annotator_id=payload.annotator_id,
            sampling_policy_version=payload.sampling_policy_version,
            sample_type=payload.sample_type,
            sample_reason=payload.sample_reason,
            item_refs=tuple(payload.item_refs),
            stratum=payload.stratum,
            due_at=payload.due_at,
            metadata=payload.metadata,
        ),
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return QualitySampleOut.from_domain(sample)


@router.get("/quality-samples/{quality_sample_id}", response_model=QualitySampleOut)
@router.get(
    "/annotation-quality-samples/{quality_sample_id}",
    response_model=QualitySampleOut,
    include_in_schema=False,
)
def get_quality_sample(
    quality_sample_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> QualitySampleOut:
    return QualitySampleOut.from_domain(use_cases.quality_samples.get(scope, quality_sample_id))


@router.post(
    "/quality-samples/{quality_sample_id}/actions/{action}",
    response_model=QualitySampleOut,
)
@router.post(
    "/annotation-quality-samples/{quality_sample_id}/actions/{action}",
    response_model=QualitySampleOut,
    include_in_schema=False,
)
def act_on_quality_sample(
    quality_sample_id: UUID,
    action: ActionName,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey = None,
    payload: QualitySampleActionRequest | None = None,
) -> QualitySampleOut:
    sample = use_cases.quality_samples.apply_action(
        scope,
        quality_sample_id,
        action,
        expected_version=expected_version,
        reviewer_id=payload.reviewer_id if payload is not None else None,
        reason=payload.reason if payload is not None else None,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return QualitySampleOut.from_domain(sample)


@router.get("/iaa-reports", response_model=PageOut[IAAReportOut])
@router.get(
    "/annotation-iaa-reports",
    response_model=PageOut[IAAReportOut],
    include_in_schema=False,
)
def list_iaa_reports(
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
    task_id: UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PageOut[IAAReportOut]:
    items = use_cases.iaa.list_for_task(scope, task_id, limit=limit)
    return PageOut(
        items=[IAAReportOut.from_record(item) for item in items],
        next_cursor=None,
    )


@router.post("/iaa-reports", response_model=IAAReportOut, status_code=201)
@router.post(
    "/annotation-iaa-reports",
    response_model=IAAReportOut,
    status_code=201,
    include_in_schema=False,
)
def create_iaa_report(
    payload: IAAReportCreate,
    request: Request,
    scope: WriteScope,
    use_cases: AnnotationUseCasesDep,
    idempotency_key: IdempotencyKey = None,
) -> IAAReportOut:
    report = use_cases.iaa.compute(
        scope,
        payload.task_id,
        metric_definitions_version=payload.metric_definitions_version,
        iou_threshold=payload.iou_threshold,
        idempotency_key=idempotency_key,
        method=request.method,
        path=request.url.path,
        request_id=request_id(request),
    )
    return IAAReportOut.from_record(report)


@router.get("/iaa-reports/{iaa_report_id}", response_model=IAAReportOut)
@router.get(
    "/annotation-iaa-reports/{iaa_report_id}",
    response_model=IAAReportOut,
    include_in_schema=False,
)
def get_iaa_report(
    iaa_report_id: UUID,
    scope: ReadScope,
    use_cases: AnnotationUseCasesDep,
) -> IAAReportOut:
    return IAAReportOut.from_record(use_cases.iaa.get(scope, iaa_report_id))
