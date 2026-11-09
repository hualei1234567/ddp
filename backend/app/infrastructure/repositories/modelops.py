"""阶段 5 SQLAlchemy 仓储。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.modelops.audit import AuditRecordData, OutboxEventData
from app.application.modelops.idempotency import IdempotencyRecordData
from app.application.modelops.ports import CallbackRecord
from app.domain.identity.models import ProjectScope
from app.domain.modelops.artifacts import ModelArtifact, ModelRuntime
from app.domain.modelops.errors import DuplicateResourceError, VersionConflictError
from app.domain.modelops.models import (
    DataRequirement,
    Evaluation,
    InferenceService,
    InferenceTask,
    Model,
    ModelFeedback,
    ModelVersion,
)
from app.infrastructure.db.models.collection import AuditLog
from app.infrastructure.db.models.collection import IdempotencyRecord as IdempotencyModel
from app.infrastructure.db.models.collection import OutboxEvent as OutboxModel
from app.infrastructure.db.models.datasets import DatasetVersionModel
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
        status=row.status,
        metrics=dict(row.metrics or {}),
        thresholds=dict(row.thresholds or {}),
        verdict=row.verdict,
        report_ref=dict(row.report_ref) if row.report_ref else None,
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


class SqlModelRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, model: Model) -> Model:
        scope.ensure(model.project_id)
        self._session.add(
            ModelRow(
                id=model.model_id,
                project_id=model.project_id,
                code=model.code,
                name=model.name,
                description=model.description,
                task_type=model.task_type,
                framework=model.framework,
                owner_id=model.owner_id,
                status=model.status,
                current_version_id=model.current_version_id,
                tags=list(model.tags or []),
                version=model.row_version,
                created_at=model.created_at,
                updated_at=model.updated_at,
                created_by=model.created_by,
                updated_by=model.updated_by,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("模型编码已存在") from exc
        return model

    def get(self, scope: ProjectScope, model_id: UUID) -> Model | None:
        row = self._session.get(ModelRow, model_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _model(row)

    def get_by_code(self, scope: ProjectScope, code: str) -> Model | None:
        row = self._session.scalar(
            select(ModelRow).where(ModelRow.project_id == scope.project_id, ModelRow.code == code)
        )
        return _model(row) if row else None

    def save(self, scope: ProjectScope, model: Model, *, expected_version: int) -> Model:
        scope.ensure(model.project_id)
        result = self._session.execute(
            update(ModelRow)
            .where(
                ModelRow.id == model.model_id,
                ModelRow.project_id == scope.project_id,
                ModelRow.version == expected_version,
            )
            .values(
                name=model.name,
                description=model.description,
                framework=model.framework,
                tags=list(model.tags or []),
                status=model.status,
                current_version_id=model.current_version_id,
                version=expected_version + 1,
                updated_at=model.updated_at or datetime.now(),
                updated_by=model.updated_by,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("模型")
        self._session.flush()
        refreshed = self.get(scope, model.model_id)
        assert refreshed is not None
        return refreshed

    def list(self, scope, *, status=None, task_type=None, after_id=None, limit=20):
        stmt = select(ModelRow).where(ModelRow.project_id == scope.project_id)
        if status:
            stmt = stmt.where(ModelRow.status == status)
        if task_type:
            stmt = stmt.where(ModelRow.task_type == task_type)
        clause = _after_clause(ModelRow.created_at, ModelRow.id, ModelRow, after_id)
        if clause is not None:
            stmt = stmt.where(clause)
        stmt = stmt.order_by(ModelRow.created_at.desc(), ModelRow.id.desc()).limit(limit)
        return [_model(row) for row in self._session.scalars(stmt)]


class SqlModelVersionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, version: ModelVersion) -> ModelVersion:
        scope.ensure(version.project_id)
        self._session.add(
            ModelVersionRow(
                id=version.model_version_id,
                project_id=version.project_id,
                model_id=version.model_id,
                version_no=version.version_no,
                status=version.status,
                artifact=dict(version.artifact.to_payload()),
                runtime=dict(version.runtime.to_payload()),
                artifact_sha256=version.artifact.sha256,
                training_request_id=version.training_request_id,
                training_delivery_id=version.training_delivery_id,
                external_ref_id=version.external_ref_id,
                dataset_version_ids=[str(item) for item in version.dataset_version_ids or []],
                metrics_summary=dict(version.metrics_summary or {}),
                release_notes=version.release_notes,
                validated_at=version.validated_at,
                released_by=version.released_by,
                released_at=version.released_at,
                deprecated_at=version.deprecated_at,
                deprecation_reason=version.deprecation_reason,
                version=version.row_version,
                created_at=version.created_at,
                updated_at=version.updated_at,
                created_by=version.created_by,
                updated_by=version.updated_by,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("模型版本已存在") from exc
        return version

    def get(self, scope: ProjectScope, version_id: UUID) -> ModelVersion | None:
        row = self._session.get(ModelVersionRow, version_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _version(row)

    def next_version_no(self, scope: ProjectScope, model_id: UUID) -> int:
        rows = self._session.scalars(
            select(ModelVersionRow.version_no).where(
                ModelVersionRow.project_id == scope.project_id,
                ModelVersionRow.model_id == model_id,
            )
        )
        values = list(rows)
        return max(values, default=0) + 1

    def save(self, scope, version: ModelVersion, *, expected_version: int) -> ModelVersion:
        scope.ensure(version.project_id)
        result = self._session.execute(
            update(ModelVersionRow)
            .where(
                ModelVersionRow.id == version.model_version_id,
                ModelVersionRow.project_id == scope.project_id,
                ModelVersionRow.version == expected_version,
            )
            .values(
                status=version.status,
                validated_at=version.validated_at,
                released_by=version.released_by,
                released_at=version.released_at,
                deprecated_at=version.deprecated_at,
                deprecation_reason=version.deprecation_reason,
                version=expected_version + 1,
                updated_at=version.updated_at or datetime.now(),
                updated_by=version.updated_by,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("模型版本")
        self._session.flush()
        refreshed = self.get(scope, version.model_version_id)
        assert refreshed is not None
        return refreshed

    def list(self, scope, model_id, *, status=None, after_id=None, limit=20):
        stmt = select(ModelVersionRow).where(
            ModelVersionRow.project_id == scope.project_id,
            ModelVersionRow.model_id == model_id,
        )
        if status:
            stmt = stmt.where(ModelVersionRow.status == status)
        clause = _after_clause(
            ModelVersionRow.created_at, ModelVersionRow.id, ModelVersionRow, after_id
        )
        if clause is not None:
            stmt = stmt.where(clause)
        stmt = stmt.order_by(ModelVersionRow.version_no.desc()).limit(limit)
        return [_version(row) for row in self._session.scalars(stmt)]


class SqlEvaluationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope, evaluation: Evaluation) -> Evaluation:
        scope.ensure(evaluation.project_id)
        self._session.add(
            EvaluationRow(
                id=evaluation.evaluation_id,
                project_id=evaluation.project_id,
                model_version_id=evaluation.model_version_id,
                dataset_version_id=evaluation.dataset_version_id,
                baseline_evaluation_id=evaluation.baseline_evaluation_id,
                status=evaluation.status,
                metrics=dict(evaluation.metrics or {}),
                thresholds=dict(evaluation.thresholds),
                verdict=evaluation.verdict,
                report_ref=dict(evaluation.report_ref) if evaluation.report_ref else None,
                error_code=evaluation.error_code,
                error_summary=evaluation.error_summary,
                started_at=evaluation.started_at,
                finished_at=evaluation.finished_at,
                version=evaluation.row_version,
                created_at=evaluation.created_at,
                updated_at=evaluation.updated_at,
                created_by=evaluation.created_by,
            )
        )
        self._session.flush()
        return evaluation

    def get(self, scope, evaluation_id: UUID) -> Evaluation | None:
        row = self._session.get(EvaluationRow, evaluation_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _evaluation(row)

    def save(self, scope, evaluation: Evaluation, *, expected_version: int) -> Evaluation:
        result = self._session.execute(
            update(EvaluationRow)
            .where(
                EvaluationRow.id == evaluation.evaluation_id,
                EvaluationRow.project_id == scope.project_id,
                EvaluationRow.version == expected_version,
            )
            .values(
                status=evaluation.status,
                metrics=dict(evaluation.metrics or {}),
                verdict=evaluation.verdict,
                report_ref=dict(evaluation.report_ref) if evaluation.report_ref else None,
                error_code=evaluation.error_code,
                error_summary=evaluation.error_summary,
                started_at=evaluation.started_at,
                finished_at=evaluation.finished_at,
                version=expected_version + 1,
                updated_at=evaluation.updated_at or datetime.now(),
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("评测")
        self._session.flush()
        refreshed = self.get(scope, evaluation.evaluation_id)
        assert refreshed is not None
        return refreshed

    def list(
        self,
        scope,
        *,
        model_version_id=None,
        dataset_version_id=None,
        status=None,
        verdict=None,
        after_id=None,
        limit=20,
    ):
        stmt = select(EvaluationRow).where(EvaluationRow.project_id == scope.project_id)
        if model_version_id:
            stmt = stmt.where(EvaluationRow.model_version_id == model_version_id)
        if dataset_version_id:
            stmt = stmt.where(EvaluationRow.dataset_version_id == dataset_version_id)
        if status:
            stmt = stmt.where(EvaluationRow.status == status)
        if verdict:
            stmt = stmt.where(EvaluationRow.verdict == verdict)
        clause = _after_clause(EvaluationRow.created_at, EvaluationRow.id, EvaluationRow, after_id)
        if clause is not None:
            stmt = stmt.where(clause)
        stmt = stmt.order_by(EvaluationRow.created_at.desc(), EvaluationRow.id.desc()).limit(limit)
        return [_evaluation(row) for row in self._session.scalars(stmt)]


class SqlInferenceServiceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope, service: InferenceService) -> InferenceService:
        self._session.add(
            InferenceServiceRow(
                id=service.service_id,
                project_id=service.project_id,
                model_version_id=service.model_version_id,
                environment=service.environment,
                endpoint_url=service.endpoint_url,
                resource_ref=service.resource_ref,
                config=dict(service.config or {}),
                status=service.status,
                health_status=service.health_status,
                deployed_at=service.deployed_at,
                stopped_at=service.stopped_at,
                version=service.row_version,
                created_at=service.created_at,
                updated_at=service.updated_at,
                created_by=service.created_by,
            )
        )
        self._session.flush()
        return service

    def get(self, scope, service_id: UUID) -> InferenceService | None:
        row = self._session.get(InferenceServiceRow, service_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _service(row)

    def save(self, scope, service: InferenceService, *, expected_version: int) -> InferenceService:
        result = self._session.execute(
            update(InferenceServiceRow)
            .where(
                InferenceServiceRow.id == service.service_id,
                InferenceServiceRow.project_id == scope.project_id,
                InferenceServiceRow.version == expected_version,
            )
            .values(
                status=service.status,
                health_status=service.health_status,
                deployed_at=service.deployed_at,
                stopped_at=service.stopped_at,
                version=expected_version + 1,
                updated_at=service.updated_at or datetime.now(),
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("推理服务")
        self._session.flush()
        refreshed = self.get(scope, service.service_id)
        assert refreshed is not None
        return refreshed

    def list(self, scope, *, status=None, after_id=None, limit=20):
        stmt = select(InferenceServiceRow).where(InferenceServiceRow.project_id == scope.project_id)
        if status:
            stmt = stmt.where(InferenceServiceRow.status == status)
        clause = _after_clause(
            InferenceServiceRow.created_at, InferenceServiceRow.id, InferenceServiceRow, after_id
        )
        if clause is not None:
            stmt = stmt.where(clause)
        stmt = stmt.order_by(
            InferenceServiceRow.created_at.desc(), InferenceServiceRow.id.desc()
        ).limit(limit)
        return [_service(row) for row in self._session.scalars(stmt)]

    def count_active_for_version(self, scope, model_version_id: UUID) -> int:
        rows = self._session.scalars(
            select(InferenceServiceRow.id).where(
                InferenceServiceRow.project_id == scope.project_id,
                InferenceServiceRow.model_version_id == model_version_id,
                InferenceServiceRow.status == "ACTIVE",
            )
        )
        return len(list(rows))


class SqlInferenceTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope, task: InferenceTask) -> InferenceTask:
        self._session.add(
            InferenceTaskRow(
                id=task.task_id,
                project_id=task.project_id,
                model_version_id=task.model_version_id,
                service_id=task.service_id,
                dataset_version_id=task.dataset_version_id,
                episode_ids=[str(item) for item in task.episode_ids or []],
                status=task.status,
                result_ref=dict(task.result_ref) if task.result_ref else None,
                metrics=dict(task.metrics or {}),
                log_ref=task.log_ref,
                error_code=task.error_code,
                error_summary=task.error_summary,
                started_at=task.started_at,
                finished_at=task.finished_at,
                version=task.row_version,
                created_at=task.created_at,
                updated_at=task.updated_at,
                created_by=task.created_by,
            )
        )
        self._session.flush()
        return task

    def get(self, scope, task_id: UUID) -> InferenceTask | None:
        row = self._session.get(InferenceTaskRow, task_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _task(row)

    def save(self, scope, task: InferenceTask, *, expected_version: int) -> InferenceTask:
        result = self._session.execute(
            update(InferenceTaskRow)
            .where(
                InferenceTaskRow.id == task.task_id,
                InferenceTaskRow.project_id == scope.project_id,
                InferenceTaskRow.version == expected_version,
            )
            .values(
                status=task.status,
                result_ref=dict(task.result_ref) if task.result_ref else None,
                metrics=dict(task.metrics or {}),
                log_ref=task.log_ref,
                error_code=task.error_code,
                error_summary=task.error_summary,
                started_at=task.started_at,
                finished_at=task.finished_at,
                version=expected_version + 1,
                updated_at=task.updated_at or datetime.now(),
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("推理任务")
        self._session.flush()
        refreshed = self.get(scope, task.task_id)
        assert refreshed is not None
        return refreshed

    def list(self, scope, *, status=None, model_version_id=None, after_id=None, limit=20):
        stmt = select(InferenceTaskRow).where(InferenceTaskRow.project_id == scope.project_id)
        if status:
            stmt = stmt.where(InferenceTaskRow.status == status)
        if model_version_id:
            stmt = stmt.where(InferenceTaskRow.model_version_id == model_version_id)
        clause = _after_clause(
            InferenceTaskRow.created_at, InferenceTaskRow.id, InferenceTaskRow, after_id
        )
        if clause is not None:
            stmt = stmt.where(clause)
        stmt = stmt.order_by(InferenceTaskRow.created_at.desc(), InferenceTaskRow.id.desc()).limit(
            limit
        )
        return [_task(row) for row in self._session.scalars(stmt)]


class SqlFeedbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope, feedback: ModelFeedback) -> ModelFeedback:
        self._session.add(
            ModelFeedbackRow(
                id=feedback.feedback_id,
                project_id=feedback.project_id,
                model_version_id=feedback.model_version_id,
                evaluation_id=feedback.evaluation_id,
                inference_task_id=feedback.inference_task_id,
                issue_type=feedback.issue_type,
                severity=feedback.severity,
                description=feedback.description,
                dimensions=[item.to_payload() for item in feedback.dimensions],
                evidence_refs=list(feedback.evidence_refs or []),
                status=feedback.status,
                converted_requirement_id=feedback.converted_requirement_id,
                resolution_note=feedback.resolution_note,
                version=feedback.row_version,
                created_at=feedback.created_at,
                updated_at=feedback.updated_at,
                created_by=feedback.created_by,
            )
        )
        self._session.flush()
        return feedback

    def get(self, scope, feedback_id: UUID) -> ModelFeedback | None:
        row = self._session.get(ModelFeedbackRow, feedback_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _feedback(row)

    def save(self, scope, feedback: ModelFeedback, *, expected_version: int) -> ModelFeedback:
        result = self._session.execute(
            update(ModelFeedbackRow)
            .where(
                ModelFeedbackRow.id == feedback.feedback_id,
                ModelFeedbackRow.project_id == scope.project_id,
                ModelFeedbackRow.version == expected_version,
            )
            .values(
                status=feedback.status,
                converted_requirement_id=feedback.converted_requirement_id,
                resolution_note=feedback.resolution_note,
                version=expected_version + 1,
                updated_at=feedback.updated_at or datetime.now(),
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("模型反馈")
        self._session.flush()
        refreshed = self.get(scope, feedback.feedback_id)
        assert refreshed is not None
        return refreshed

    def list(
        self, scope, *, status=None, issue_type=None, model_version_id=None, after_id=None, limit=20
    ):
        stmt = select(ModelFeedbackRow).where(ModelFeedbackRow.project_id == scope.project_id)
        if status:
            stmt = stmt.where(ModelFeedbackRow.status == status)
        if issue_type:
            stmt = stmt.where(ModelFeedbackRow.issue_type == issue_type)
        if model_version_id:
            stmt = stmt.where(ModelFeedbackRow.model_version_id == model_version_id)
        clause = _after_clause(
            ModelFeedbackRow.created_at, ModelFeedbackRow.id, ModelFeedbackRow, after_id
        )
        if clause is not None:
            stmt = stmt.where(clause)
        stmt = stmt.order_by(ModelFeedbackRow.created_at.desc(), ModelFeedbackRow.id.desc()).limit(
            limit
        )
        return [_feedback(row) for row in self._session.scalars(stmt)]

    def list_open_dimensions(self, scope, *, dimension_type=None):
        rows = self._session.scalars(
            select(ModelFeedbackRow).where(
                ModelFeedbackRow.project_id == scope.project_id,
                ModelFeedbackRow.status.in_(["OPEN", "TRIAGED"]),
            )
        )
        items = [_feedback(row) for row in rows]
        if dimension_type is None:
            return items
        return [
            item
            for item in items
            if any(d.dimension_type == dimension_type for d in item.dimensions)
        ]


class SqlRequirementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope, requirement: DataRequirement) -> DataRequirement:
        self._session.add(
            DataRequirementRow(
                id=requirement.requirement_id,
                project_id=requirement.project_id,
                code=requirement.code,
                title=requirement.title,
                description=requirement.description,
                source_type=requirement.source_type,
                source_feedback_ids=[str(item) for item in requirement.source_feedback_ids or []],
                source_evaluation_ids=[
                    str(item) for item in requirement.source_evaluation_ids or []
                ],
                dimensions=[item.to_payload() for item in requirement.dimensions],
                target_episode_count=requirement.target_episode_count,
                quality_thresholds=dict(requirement.quality_thresholds or {}),
                priority=requirement.priority,
                due_at=requirement.due_at,
                owner_id=requirement.owner_id,
                status=requirement.status,
                linked_dataset_version_ids=[
                    str(item) for item in requirement.linked_dataset_version_ids or []
                ],
                retest_evaluation_ids=[
                    str(item) for item in requirement.retest_evaluation_ids or []
                ],
                satisfied_at=requirement.satisfied_at,
                satisfaction_evidence_ref=requirement.satisfaction_evidence_ref,
                version=requirement.row_version,
                created_at=requirement.created_at,
                updated_at=requirement.updated_at,
                created_by=requirement.created_by,
                updated_by=requirement.updated_by,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("数据需求编码已存在") from exc
        return requirement

    def get(self, scope, requirement_id: UUID) -> DataRequirement | None:
        row = self._session.get(DataRequirementRow, requirement_id)
        if row is None or row.project_id != scope.project_id:
            return None
        return _requirement(row)

    def get_by_code(self, scope, code: str) -> DataRequirement | None:
        row = self._session.scalar(
            select(DataRequirementRow).where(
                DataRequirementRow.project_id == scope.project_id,
                DataRequirementRow.code == code,
            )
        )
        return _requirement(row) if row else None

    def save(
        self, scope, requirement: DataRequirement, *, expected_version: int
    ) -> DataRequirement:
        result = self._session.execute(
            update(DataRequirementRow)
            .where(
                DataRequirementRow.id == requirement.requirement_id,
                DataRequirementRow.project_id == scope.project_id,
                DataRequirementRow.version == expected_version,
            )
            .values(
                status=requirement.status,
                linked_dataset_version_ids=[
                    str(item) for item in requirement.linked_dataset_version_ids or []
                ],
                retest_evaluation_ids=[
                    str(item) for item in requirement.retest_evaluation_ids or []
                ],
                satisfied_at=requirement.satisfied_at,
                satisfaction_evidence_ref=requirement.satisfaction_evidence_ref,
                version=expected_version + 1,
                updated_at=requirement.updated_at or datetime.now(),
                updated_by=requirement.updated_by,
            )
        )
        if result.rowcount != 1:
            raise VersionConflictError("数据需求")
        self._session.flush()
        refreshed = self.get(scope, requirement.requirement_id)
        assert refreshed is not None
        return refreshed

    def list(self, scope, *, status=None, priority=None, after_id=None, limit=20):
        stmt = select(DataRequirementRow).where(DataRequirementRow.project_id == scope.project_id)
        if status:
            stmt = stmt.where(DataRequirementRow.status == status)
        if priority:
            stmt = stmt.where(DataRequirementRow.priority == priority)
        clause = _after_clause(
            DataRequirementRow.created_at, DataRequirementRow.id, DataRequirementRow, after_id
        )
        if clause is not None:
            stmt = stmt.where(clause)
        stmt = stmt.order_by(
            DataRequirementRow.created_at.desc(), DataRequirementRow.id.desc()
        ).limit(limit)
        return [_requirement(row) for row in self._session.scalars(stmt)]

    def list_by_feedback(self, scope, feedback_id: UUID):
        rows = self._session.scalars(
            select(DataRequirementRow).where(DataRequirementRow.project_id == scope.project_id)
        )
        return [
            _requirement(row) for row in rows if str(feedback_id) in (row.source_feedback_ids or [])
        ]


class SqlModelAuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, scope: ProjectScope, record: AuditRecordData) -> None:
        scope.ensure(record.project_id)
        self._session.add(
            AuditLog(
                id=record.audit_id,
                project_id=record.project_id,
                actor_user_id=record.actor_user_id,
                actor_role=record.actor_role,
                resource_type=record.resource_type,
                resource_id=record.resource_id,
                action=record.action,
                from_status=record.from_status,
                to_status=record.to_status,
                reason=record.reason,
                request_id=record.request_id,
                result=record.result,
                correlation_id=None,
                details=dict(record.details),
                occurred_at=record.occurred_at,
            )
        )
        self._session.flush()


class SqlModelOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, scope: ProjectScope, event: OutboxEventData) -> None:
        scope.ensure(event.project_id)
        self._session.add(
            OutboxModel(
                id=event.event_id,
                project_id=event.project_id,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                event_type=event.event_type,
                payload=dict(event.payload),
                status="PENDING",
                attempts=0,
                available_at=event.occurred_at,
                created_at=event.occurred_at,
            )
        )
        self._session.flush()


class SqlModelIdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, scope: ProjectScope, key: str):
        row = self._session.scalar(
            select(IdempotencyModel).where(
                IdempotencyModel.project_id == scope.project_id,
                IdempotencyModel.user_id == scope.actor_id,
                IdempotencyModel.key == key,
            )
        )
        if row is None:
            return None
        from app.application.modelops.idempotency import IdempotencyRecordData

        return IdempotencyRecordData(
            project_id=row.project_id,
            user_id=row.user_id,
            key=row.key,
            method=row.method,
            path=row.path,
            request_hash=row.request_hash,
            response_status=row.response_status,
            response_ref=row.response_ref,
            created_at=_aware(row.created_at),
            expires_at=row.expires_at,
        )

    def add(self, scope: ProjectScope, record: IdempotencyRecordData) -> None:
        self._session.add(
            IdempotencyModel(
                project_id=record.project_id,
                user_id=record.user_id,
                key=record.key,
                method=record.method,
                path=record.path,
                request_hash=record.request_hash,
                response_status=record.response_status,
                response_ref=record.response_ref,
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )
        self._session.flush()


class SqlDatasetGate:
    def __init__(self, session: Session) -> None:
        self._session = session

    def version_state(self, project_id: UUID, version_id: UUID):
        row = self._session.get(DatasetVersionModel, version_id)
        if row is None or row.project_id != project_id:
            return None
        return row.status, row.validity_status

    def item_count(self, project_id: UUID, version_id: UUID) -> int:
        row = self._session.get(DatasetVersionModel, version_id)
        if row is None or row.project_id != project_id:
            return 0
        return row.item_count


class SqlModelCallbackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_event(self, adapter_id: str, event_id: str) -> CallbackRecord | None:
        row = self._session.get(ModelCallbackRow, {"adapter_id": adapter_id, "event_id": event_id})
        return _callback(row) if row else None

    def get_by_nonce(self, adapter_id: str, nonce: str) -> CallbackRecord | None:
        row = self._session.scalar(
            select(ModelCallbackRow).where(
                ModelCallbackRow.adapter_id == adapter_id,
                ModelCallbackRow.nonce == nonce,
            )
        )
        return _callback(row) if row else None

    def add(self, record: CallbackRecord) -> None:
        self._session.add(
            ModelCallbackRow(
                adapter_id=record.adapter_id,
                event_id=record.event_id,
                nonce=record.nonce,
                payload_sha256=record.payload_sha256,
                received_at=record.received_at,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("模型回调已存在") from exc

    def prune_before(self, *, received_before: datetime) -> None:
        rows = list(
            self._session.scalars(
                select(ModelCallbackRow).where(ModelCallbackRow.received_at < received_before)
            )
        )
        for row in rows:
            self._session.delete(row)
        self._session.flush()


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
