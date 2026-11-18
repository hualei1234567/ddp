"""阶段 5 仓储的共享辅助与行转换。

从 ``modelops.py`` 拆出：该文件原有 1011 行，超过 800 行硬上限。
本模块只做 ORM 行 → 领域对象的映射与分页游标构造，不含任何查询逻辑，
因此拆分后行为不变（同一函数、同一实现，仅移动位置）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import and_, or_, select

from app.application.modelops.ports import CallbackRecord
from app.domain.modelops.artifacts import ModelArtifact, ModelRuntime
from app.domain.modelops.models import (
    DataRequirement,
    Evaluation,
    InferenceService,
    InferenceTask,
    Model,
    ModelFeedback,
    ModelVersion,
)
from app.infrastructure.db.models.modelops import (
    DataRequirementRow,
    EvaluationRow,
    InferenceServiceRow,
    InferenceTaskRow,
    ModelCallbackRow,
    ModelFeedbackRow,
    ModelRow,
    ModelVersionRow,
)


def _after_clause(column_created, column_id, model, after_id: UUID | None):
    if after_id is None:
        return None
    created = select(model.created_at).where(model.id == after_id).scalar_subquery()
    return or_(
        column_created < created,
        and_(column_created == created, column_id < after_id),
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _uuids(values) -> list[UUID]:
    return [UUID(str(item)) for item in values or []]


def _model(row: ModelRow) -> Model:
    return Model(
        model_id=row.id,
        project_id=row.project_id,
        code=row.code,
        name=row.name,
        description=row.description,
        task_type=row.task_type,
        framework=row.framework,
        owner_id=row.owner_id,
        status=row.status,
        current_version_id=row.current_version_id,
        tags=list(row.tags or []),
        row_version=row.version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _version(row: ModelVersionRow) -> ModelVersion:
    return ModelVersion(
        model_version_id=row.id,
        model_id=row.model_id,
        project_id=row.project_id,
        version_no=row.version_no,
        status=row.status,
        artifact=ModelArtifact.from_payload(row.artifact),
        runtime=ModelRuntime.from_payload(row.runtime),
        training_request_id=row.training_request_id,
        training_delivery_id=row.training_delivery_id,
        external_ref_id=row.external_ref_id,
        dataset_version_ids=_uuids(row.dataset_version_ids),
        metrics_summary=dict(row.metrics_summary or {}),
        release_notes=row.release_notes,
        validated_at=_aware(row.validated_at),
        released_by=row.released_by,
        released_at=_aware(row.released_at),
        deprecated_at=_aware(row.deprecated_at),
        deprecation_reason=row.deprecation_reason,
        row_version=row.version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _evaluation(row: EvaluationRow) -> Evaluation:
    return Evaluation(
        evaluation_id=row.id,
        project_id=row.project_id,
        model_version_id=row.model_version_id,
        dataset_version_id=row.dataset_version_id,
        baseline_evaluation_id=row.baseline_evaluation_id,
        baseline_dataset_version_id=row.baseline_dataset_version_id,
        status=row.status,
        metrics=dict(row.metrics or {}),
        thresholds=dict(row.thresholds or {}),
        verdict=row.verdict,
        report_ref=dict(row.report_ref) if row.report_ref else None,
        failure_cases=list(row.failure_cases or []),
        error_code=row.error_code,
        error_summary=row.error_summary,
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        row_version=row.version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        created_by=row.created_by,
    )


def _service(row: InferenceServiceRow) -> InferenceService:
    return InferenceService(
        service_id=row.id,
        project_id=row.project_id,
        model_version_id=row.model_version_id,
        environment=row.environment,
        endpoint_url=row.endpoint_url,
        resource_ref=row.resource_ref,
        config=dict(row.config or {}),
        status=row.status,
        health_status=row.health_status,
        deployed_at=_aware(row.deployed_at),
        stopped_at=_aware(row.stopped_at),
        row_version=row.version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        created_by=row.created_by,
    )


def _task(row: InferenceTaskRow) -> InferenceTask:
    return InferenceTask(
        task_id=row.id,
        project_id=row.project_id,
        model_version_id=row.model_version_id,
        service_id=row.service_id,
        dataset_version_id=row.dataset_version_id,
        episode_ids=_uuids(row.episode_ids),
        status=row.status,
        result_ref=dict(row.result_ref) if row.result_ref else None,
        metrics=dict(row.metrics or {}),
        log_ref=row.log_ref,
        error_code=row.error_code,
        error_summary=row.error_summary,
        started_at=_aware(row.started_at),
        finished_at=_aware(row.finished_at),
        row_version=row.version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        created_by=row.created_by,
    )


def _feedback(row: ModelFeedbackRow) -> ModelFeedback:
    from app.domain.modelops.gaps import GapDimension

    return ModelFeedback(
        feedback_id=row.id,
        project_id=row.project_id,
        model_version_id=row.model_version_id,
        evaluation_id=row.evaluation_id,
        inference_task_id=row.inference_task_id,
        issue_type=row.issue_type,
        severity=row.severity,
        description=row.description,
        dimensions=[GapDimension.from_payload(item) for item in row.dimensions or []],
        evidence_refs=list(row.evidence_refs or []),
        status=row.status,
        converted_requirement_id=row.converted_requirement_id,
        resolution_note=row.resolution_note,
        row_version=row.version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        created_by=row.created_by,
    )


def _requirement(row: DataRequirementRow) -> DataRequirement:
    from app.domain.modelops.gaps import GapDimension

    return DataRequirement(
        requirement_id=row.id,
        project_id=row.project_id,
        code=row.code,
        title=row.title,
        description=row.description,
        source_type=row.source_type,
        source_feedback_ids=_uuids(row.source_feedback_ids),
        source_evaluation_ids=_uuids(row.source_evaluation_ids),
        dimensions=[GapDimension.from_payload(item) for item in row.dimensions or []],
        target_episode_count=row.target_episode_count,
        quality_thresholds=dict(row.quality_thresholds or {}),
        priority=row.priority,
        due_at=_aware(row.due_at),
        owner_id=row.owner_id,
        status=row.status,
        linked_dataset_version_ids=_uuids(row.linked_dataset_version_ids),
        retest_evaluation_ids=_uuids(row.retest_evaluation_ids),
        satisfied_at=_aware(row.satisfied_at),
        satisfaction_evidence_ref=row.satisfaction_evidence_ref,
        row_version=row.version,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _callback(row: ModelCallbackRow) -> CallbackRecord:
    from app.application.modelops.callbacks import CallbackRecordData

    return CallbackRecordData(
        callback_id=uuid5(NAMESPACE_URL, f"{row.adapter_id}:{row.event_id}"),
        adapter_id=row.adapter_id,
        event_id=row.event_id,
        nonce=row.nonce,
        payload_sha256=row.payload_sha256,
        received_at=row.received_at,
    )
