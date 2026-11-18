"""阶段 5 HTTP 响应模型（用于 ``response_model`` 声明与契约比对）。

与 ``modelops_schemas.py`` 分文件：后者只放请求模型（``extra="forbid"``），
本文件只放响应模型，避免同一文件同时承载"严格入参"与"完整出参"两种语义。

字段集合以**运行时真实返回**为准，并与
``packages/contracts/fragments/schemas.stage5.yaml`` 的同名 schema 对齐；
已知差异（如 ``artifact.verification_status`` 契约未声明）在模块文档与交付
报告中登记，不在此处丢弃真实字段。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.modelops.failure_cases import FailureCase
from app.domain.modelops.gaps import GapSummary, GapSummaryItem
from app.domain.modelops.models import (
    DataRequirement,
    Evaluation,
    InferenceService,
    InferenceTask,
    Model,
    ModelFeedback,
    ModelVersion,
)


class ModelArtifactOut(BaseModel):
    """模型产物引用。

    注意：``verification_status`` 是运行时真实返回值（前端
    ``apps/web/src/types/modelops.ts`` 也读取它），但冻结契约的 ``ModelArtifact``
    声明为 ``additionalProperties: false`` 且只含 4 个字段。契约侧待补齐，
    实现侧不得为通过比对而丢弃该字段。
    """

    object_key: str
    sha256: str
    size_bytes: int
    format: str
    verification_status: str


class ModelRuntimeOut(BaseModel):
    framework: str
    framework_version: str
    python_version: str | None = None
    cuda_version: str | None = None
    image_ref: str | None = None


class GapDimensionOut(BaseModel):
    dimension_type: str
    dimension_value: str
    current_count: int | None = None
    target_count: int | None = None


class ModelOut(BaseModel):
    model_id: UUID
    project_id: UUID
    code: str
    name: str
    description: str | None = None
    task_type: str
    framework: str
    owner_id: UUID
    status: str
    current_version_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, model: Model) -> ModelOut:
        return cls(
            model_id=model.model_id,
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
            allowed_actions=sorted(model.allowed_actions),
            version=model.row_version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class ModelVersionOut(BaseModel):
    model_version_id: UUID
    model_id: UUID
    project_id: UUID
    version_no: int
    status: str
    artifact: ModelArtifactOut
    runtime: ModelRuntimeOut
    training_request_id: UUID | None = None
    training_delivery_id: UUID | None = None
    external_ref_id: UUID | None = None
    dataset_version_ids: list[str] = Field(default_factory=list)
    metrics_summary: dict[str, float] = Field(default_factory=dict)
    release_notes: str | None = None
    validated_at: datetime | None = None
    released_by: UUID | None = None
    released_at: datetime | None = None
    deprecated_at: datetime | None = None
    deprecation_reason: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, version: ModelVersion) -> ModelVersionOut:
        return cls(
            model_version_id=version.model_version_id,
            model_id=version.model_id,
            project_id=version.project_id,
            version_no=version.version_no,
            status=version.status,
            artifact=ModelArtifactOut(**version.artifact.to_payload()),
            runtime=ModelRuntimeOut(**version.runtime.to_payload()),
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
            allowed_actions=sorted(version.allowed_actions),
            version=version.row_version,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )


class FailureCaseOut(BaseModel):
    id: str
    category: str
    severity: str
    episode_key: str | None = None
    episode_id: UUID | None = None
    evidence: dict[str, object] | str | None = None
    metric: str | None = None
    observed: float | str | None = None
    expected: float | str | None = None
    suggested_gap: str | None = None

    @classmethod
    def from_domain(cls, case: FailureCase) -> FailureCaseOut:
        return cls(**case.to_dict())


class EvaluationOut(BaseModel):
    evaluation_id: UUID
    project_id: UUID
    model_version_id: UUID
    dataset_version_id: UUID
    baseline_evaluation_id: UUID | None = None
    baseline_dataset_version_id: UUID | None = None
    status: str
    metrics: dict[str, float]
    thresholds: dict[str, dict[str, float | None]]
    verdict: str
    report_ref: dict[str, object] | None = None
    # 写入上限 200 条 / 64 KiB；读取随评测整体返回（等价于固定上限分页）。
    failure_cases: list[FailureCaseOut] = Field(default_factory=list)
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, evaluation: Evaluation) -> EvaluationOut:
        return cls(
            evaluation_id=evaluation.evaluation_id,
            project_id=evaluation.project_id,
            model_version_id=evaluation.model_version_id,
            dataset_version_id=evaluation.dataset_version_id,
            baseline_evaluation_id=evaluation.baseline_evaluation_id,
            baseline_dataset_version_id=evaluation.baseline_dataset_version_id,
            status=evaluation.status,
            metrics=dict(evaluation.metrics or {}),
            thresholds=evaluation.thresholds,
            verdict=evaluation.verdict,
            report_ref=evaluation.report_ref,
            failure_cases=[FailureCaseOut.from_domain(case) for case in evaluation.failure_cases],
            error_code=evaluation.error_code,
            error_summary=evaluation.error_summary,
            started_at=evaluation.started_at,
            finished_at=evaluation.finished_at,
            allowed_actions=sorted(evaluation.allowed_actions),
            version=evaluation.row_version,
            created_at=evaluation.created_at,
            updated_at=evaluation.updated_at,
        )


class InferenceServiceOut(BaseModel):
    service_id: UUID
    project_id: UUID
    model_version_id: UUID
    environment: str
    endpoint_url: str
    resource_ref: str | None = None
    config: dict[str, object] = Field(default_factory=dict)
    status: str
    health_status: str
    deployed_at: datetime | None = None
    stopped_at: datetime | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, service: InferenceService) -> InferenceServiceOut:
        return cls(
            service_id=service.service_id,
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
            allowed_actions=sorted(service.allowed_actions),
            version=service.row_version,
            created_at=service.created_at,
            updated_at=service.updated_at,
        )


class InferenceTaskOut(BaseModel):
    task_id: UUID
    project_id: UUID
    model_version_id: UUID
    service_id: UUID | None = None
    dataset_version_id: UUID | None = None
    episode_ids: list[str] = Field(default_factory=list)
    status: str
    result_ref: dict[str, object] | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    log_ref: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, task: InferenceTask) -> InferenceTaskOut:
        return cls(
            task_id=task.task_id,
            project_id=task.project_id,
            model_version_id=task.model_version_id,
            service_id=task.service_id,
            dataset_version_id=task.dataset_version_id,
            episode_ids=[str(item) for item in task.episode_ids or []],
            status=task.status,
            result_ref=task.result_ref,
            metrics=dict(task.metrics or {}),
            log_ref=task.log_ref,
            error_code=task.error_code,
            error_summary=task.error_summary,
            started_at=task.started_at,
            finished_at=task.finished_at,
            allowed_actions=sorted(task.allowed_actions),
            version=task.row_version,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class FeedbackOut(BaseModel):
    feedback_id: UUID
    project_id: UUID
    model_version_id: UUID
    evaluation_id: UUID | None = None
    inference_task_id: UUID | None = None
    issue_type: str
    severity: str
    description: str
    dimensions: list[GapDimensionOut]
    evidence_refs: list[str] = Field(default_factory=list)
    status: str
    converted_requirement_id: UUID | None = None
    resolution_note: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, feedback: ModelFeedback) -> FeedbackOut:
        return cls(
            feedback_id=feedback.feedback_id,
            project_id=feedback.project_id,
            model_version_id=feedback.model_version_id,
            evaluation_id=feedback.evaluation_id,
            inference_task_id=feedback.inference_task_id,
            issue_type=feedback.issue_type,
            severity=feedback.severity,
            description=feedback.description,
            dimensions=[GapDimensionOut(**item.to_payload()) for item in feedback.dimensions],
            evidence_refs=list(feedback.evidence_refs or []),
            status=feedback.status,
            converted_requirement_id=feedback.converted_requirement_id,
            resolution_note=feedback.resolution_note,
            allowed_actions=sorted(feedback.allowed_actions),
            version=feedback.row_version,
            created_at=feedback.created_at,
            updated_at=feedback.updated_at,
        )


class DataRequirementOut(BaseModel):
    requirement_id: UUID
    project_id: UUID
    code: str
    title: str
    description: str | None = None
    source_type: str
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_evaluation_ids: list[str] = Field(default_factory=list)
    dimensions: list[GapDimensionOut]
    target_episode_count: int = 1
    quality_thresholds: dict[str, object] = Field(default_factory=dict)
    priority: str
    due_at: datetime | None = None
    owner_id: UUID | None = None
    status: str
    linked_dataset_version_ids: list[str] = Field(default_factory=list)
    retest_evaluation_ids: list[str] = Field(default_factory=list)
    satisfied_at: datetime | None = None
    satisfaction_evidence_ref: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    version: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_domain(cls, requirement: DataRequirement) -> DataRequirementOut:
        return cls(
            requirement_id=requirement.requirement_id,
            project_id=requirement.project_id,
            code=requirement.code,
            title=requirement.title,
            description=requirement.description,
            source_type=requirement.source_type,
            source_feedback_ids=[str(item) for item in requirement.source_feedback_ids or []],
            source_evaluation_ids=[str(item) for item in requirement.source_evaluation_ids or []],
            dimensions=[GapDimensionOut(**item.to_payload()) for item in requirement.dimensions],
            target_episode_count=requirement.target_episode_count,
            quality_thresholds=dict(requirement.quality_thresholds or {}),
            priority=requirement.priority,
            due_at=requirement.due_at,
            owner_id=requirement.owner_id,
            status=requirement.status,
            linked_dataset_version_ids=[
                str(item) for item in requirement.linked_dataset_version_ids or []
            ],
            retest_evaluation_ids=[str(item) for item in requirement.retest_evaluation_ids or []],
            satisfied_at=requirement.satisfied_at,
            satisfaction_evidence_ref=requirement.satisfaction_evidence_ref,
            allowed_actions=sorted(requirement.allowed_actions),
            version=requirement.row_version,
            created_at=requirement.created_at,
            updated_at=requirement.updated_at,
        )


class DataGapSummaryItemOut(BaseModel):
    dimension_type: str
    dimension_value: str
    open_feedback_count: int
    current_count: int | None = None
    target_count: int | None = None
    suggested_episode_count: int
    requirement_ids: list[str] = Field(default_factory=list)
    # 契约尚未收录（缺口已登记）：实测 MEASURED / 人工 MANUAL / 未知 UNKNOWN。
    coverage_source: str = "UNKNOWN"

    @classmethod
    def from_domain(cls, item: GapSummaryItem) -> DataGapSummaryItemOut:
        return cls(**item.to_payload())  # type: ignore[arg-type]


class DataGapSummaryOut(BaseModel):
    items: list[DataGapSummaryItemOut]
    freshness: str

    @classmethod
    def from_domain(cls, summary: GapSummary) -> DataGapSummaryOut:
        return cls(
            items=[DataGapSummaryItemOut.from_domain(item) for item in summary.items],
            freshness=summary.freshness,
        )


__all__ = [
    "DataGapSummaryItemOut",
    "DataGapSummaryOut",
    "DataRequirementOut",
    "EvaluationOut",
    "FeedbackOut",
    "GapDimensionOut",
    "InferenceServiceOut",
    "InferenceTaskOut",
    "ModelArtifactOut",
    "ModelOut",
    "ModelRuntimeOut",
    "ModelVersionOut",
]
