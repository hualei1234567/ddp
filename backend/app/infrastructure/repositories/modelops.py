"""阶段 5 SQLAlchemy 仓储。

行转换与分页辅助已拆到 ``modelops_rows``（本文件原 1011 行，超过 800 行上限）。
仓储类之间的复用通过 ``from ...modelops_rows import _model`` 等方式保持，
调用点无需改动。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
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
from app.infrastructure.db.models.modelops import (
    DataRequirementRow,
    EvaluationRow,
    InferenceServiceRow,
    InferenceTaskRow,
    ModelFeedbackRow,
    ModelRow,
    ModelVersionRow,
)

# 行转换与分页辅助：拆分后从 modelops_rows 重新导出，保持既有调用点与
# 测试里 ``from ...modelops import _model`` 的用法不变。
from app.infrastructure.repositories.modelops_rows import (  # noqa: F401
    _after_clause,
    _aware,
    _callback,
    _evaluation,
    _feedback,
    _model,
    _requirement,
    _service,
    _task,
    _uuids,
    _version,
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
                # 校验结论（verification_status）写在 artifact 里，必须随 save
                # 一起落库；只更新版本状态会让界面永远显示 UNVERIFIED。
                artifact=version.artifact.to_payload(),
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
                baseline_dataset_version_id=evaluation.baseline_dataset_version_id,
                status=evaluation.status,
                metrics=dict(evaluation.metrics or {}),
                thresholds=dict(evaluation.thresholds),
                verdict=evaluation.verdict,
                report_ref=dict(evaluation.report_ref) if evaluation.report_ref else None,
                failure_cases=[case.to_dict() for case in evaluation.failure_cases],
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
                failure_cases=[case.to_dict() for case in evaluation.failure_cases],
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


# 跨模块辅助仓储拆到 modelops_aux.py；此处重新导出，依赖注入调用点不变。
from app.infrastructure.repositories.modelops_aux import (  # noqa: E402,F401
    SqlDatasetGate,
    SqlModelAuditRepository,
    SqlModelCallbackRepository,
    SqlModelIdempotencyRepository,
    SqlModelOutboxRepository,
)
