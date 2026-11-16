"""阶段 5 领域实体：Model/ModelVersion/Evaluation/Inference/Feedback/DataRequirement。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from app.domain.modelops.artifacts import ModelArtifact, ModelRuntime
from app.domain.modelops.enums import (
    EvaluationStatus,
    EvaluationVerdict,
    FeedbackIssueType,
    FeedbackSeverity,
    FeedbackStatus,
    HealthStatus,
    InferenceEnvironment,
    InferenceServiceStatus,
    InferenceTaskStatus,
    ModelStatus,
    ModelTaskType,
    ModelVersionStatus,
    RequirementPriority,
    RequirementSourceType,
    RequirementStatus,
)
from app.domain.modelops.errors import (
    DomainValidationError,
    EvaluationDatasetNotReadyError,
    EvaluationImmutableError,
    ModelVersionImmutableError,
    ModelVersionInUseError,
)
from app.domain.modelops.evaluation import compute_verdict, parse_metrics, parse_thresholds
from app.domain.modelops.gaps import GapDimension, parse_dimensions
from app.domain.modelops.state_machines import allowed_actions, target_status
from app.domain.modelops.validation import (
    aware_datetime,
    coerce_enum,
    non_negative_int,
    number_map,
    optional_aware_datetime,
    optional_text,
    optional_uuid,
    positive_int,
    required_uuid,
    sha256_value,
    string_list,
    text_value,
)


def _transition(family: str, current: str, action: str) -> str:
    return target_status(family, current, action)


@dataclass(frozen=True, slots=True)
class Model:
    model_id: UUID
    project_id: UUID
    code: str
    name: str
    task_type: str
    framework: str
    owner_id: UUID
    status: str = ModelStatus.DRAFT
    description: str | None = None
    current_version_id: UUID | None = None
    tags: list[str] | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("model_id", "project_id", "owner_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "code", text_value(self.code, "code", max_length=64))
        object.__setattr__(self, "name", text_value(self.name, "name", max_length=200))
        object.__setattr__(
            self, "task_type", coerce_enum(self.task_type, ModelTaskType, "task_type").value
        )
        object.__setattr__(
            self, "framework", text_value(self.framework, "framework", max_length=100)
        )
        object.__setattr__(
            self, "description", optional_text(self.description, "description", max_length=2000)
        )
        object.__setattr__(self, "status", coerce_enum(self.status, ModelStatus, "status").value)
        object.__setattr__(
            self, "current_version_id", optional_uuid(self.current_version_id, "current_version_id")
        )
        object.__setattr__(self, "tags", string_list(self.tags, "tags", max_length=64))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("model", self.status)


def new_model(
    *,
    model_id: UUID,
    project_id: UUID,
    code: str,
    name: str,
    task_type: str,
    framework: str,
    owner_id: UUID,
    description: str | None,
    tags: list[str] | None,
    actor_id: UUID,
    now: datetime,
) -> Model:
    return Model(
        model_id=model_id,
        project_id=project_id,
        code=code,
        name=name,
        task_type=task_type,
        framework=framework,
        owner_id=owner_id,
        description=description,
        tags=tags or [],
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


@dataclass(frozen=True, slots=True)
class ModelVersion:
    model_version_id: UUID
    model_id: UUID
    project_id: UUID
    version_no: int
    artifact: ModelArtifact
    runtime: ModelRuntime
    status: str = ModelVersionStatus.DRAFT
    training_request_id: UUID | None = None
    training_delivery_id: UUID | None = None
    external_ref_id: UUID | None = None
    dataset_version_ids: list[UUID] | None = None
    metrics_summary: dict[str, float] | None = None
    release_notes: str | None = None
    validated_at: datetime | None = None
    released_by: UUID | None = None
    released_at: datetime | None = None
    deprecated_at: datetime | None = None
    deprecation_reason: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("model_version_id", "model_id", "project_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "version_no", positive_int(self.version_no, "version_no"))
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, ModelVersionStatus, "status").value,
        )
        for name in ("training_request_id", "training_delivery_id", "external_ref_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        dataset_ids: list[UUID] = []
        for item in self.dataset_version_ids or []:
            if not isinstance(item, UUID):
                raise DomainValidationError("dataset_version_ids 必须是 UUID 数组")
            if item not in dataset_ids:
                dataset_ids.append(item)
        object.__setattr__(self, "dataset_version_ids", dataset_ids)
        object.__setattr__(
            self, "metrics_summary", number_map(self.metrics_summary or {}, "metrics_summary")
        )
        object.__setattr__(
            self,
            "release_notes",
            optional_text(self.release_notes, "release_notes", max_length=4000),
        )
        for name in ("validated_at", "released_at", "deprecated_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        for name in ("released_by", "created_by", "updated_by"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "deprecation_reason",
            optional_text(self.deprecation_reason, "deprecation_reason", max_length=2000),
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("model-version", self.status)

    def assert_mutable(self) -> None:
        if ModelVersionStatus(self.status) is ModelVersionStatus.RELEASED:
            raise ModelVersionImmutableError("已发布版本不可原地修改")


def new_model_version(
    *,
    model_version_id: UUID,
    model_id: UUID,
    project_id: UUID,
    version_no: int,
    artifact: ModelArtifact,
    runtime: ModelRuntime,
    training_request_id: UUID | None,
    training_delivery_id: UUID | None,
    external_ref_id: UUID | None,
    dataset_version_ids: list[UUID] | None,
    metrics_summary: dict[str, float] | None,
    release_notes: str | None,
    actor_id: UUID,
    now: datetime,
) -> ModelVersion:
    return ModelVersion(
        model_version_id=model_version_id,
        model_id=model_id,
        project_id=project_id,
        version_no=version_no,
        artifact=artifact,
        runtime=runtime,
        training_request_id=training_request_id,
        training_delivery_id=training_delivery_id,
        external_ref_id=external_ref_id,
        dataset_version_ids=dataset_version_ids or [],
        metrics_summary=metrics_summary or {},
        release_notes=release_notes,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


@dataclass(frozen=True, slots=True)
class Evaluation:
    evaluation_id: UUID
    project_id: UUID
    model_version_id: UUID
    dataset_version_id: UUID
    thresholds: dict[str, dict[str, float | None]]
    status: str = EvaluationStatus.QUEUED
    baseline_evaluation_id: UUID | None = None
    # 基线评测**当时**所用数据集版本的快照。跨数据集对比放开后，结论必须能
    # 回答"基线是哪个数据集版本、当前是哪个"；本字段让客户端与用例层不必
    # 每次都回查基线行就能判断"这次对比跨不跨数据集"。
    #
    # 这是**刻意的冗余**（基线行的 ``dataset_version_id`` 已受不可变守卫保护，
    # 理论上可回查）：冗余换来字段自足与契约可直接表达。代价是可能不一致，
    # 因此用下面的成对校验排除"只有一半"的写入；而"两值不同"是合法的
    # ——跨数据集对比正是本字段存在的意义。详见迁移 0041 的模块文档。
    # 同数据集版本时与 ``dataset_version_id`` 相等，不引入第二套语义。
    baseline_dataset_version_id: UUID | None = None
    metrics: dict[str, float] | None = None
    verdict: str = EvaluationVerdict.UNKNOWN
    report_ref: dict[str, object] | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("evaluation_id", "project_id", "model_version_id", "dataset_version_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "thresholds", parse_thresholds(self.thresholds))
        object.__setattr__(
            self, "status", coerce_enum(self.status, EvaluationStatus, "status").value
        )
        object.__setattr__(
            self,
            "baseline_evaluation_id",
            optional_uuid(self.baseline_evaluation_id, "baseline_evaluation_id"),
        )
        baseline_dataset_version_id = optional_uuid(
            self.baseline_dataset_version_id, "baseline_dataset_version_id"
        )
        # 没有基线就不该有"基线数据集版本"；有基线却不知道对方数据集版本，
        # 等于留下一个半截引用（与 0040 对 sop_version 的口径一致）。
        if (self.baseline_evaluation_id is None) != (baseline_dataset_version_id is None):
            raise DomainValidationError(
                "baseline_dataset_version_id 必须与 baseline_evaluation_id 成对出现"
            )
        object.__setattr__(self, "baseline_dataset_version_id", baseline_dataset_version_id)
        object.__setattr__(self, "metrics", parse_metrics(self.metrics))
        object.__setattr__(
            self, "verdict", coerce_enum(self.verdict, EvaluationVerdict, "verdict").value
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("evaluation", self.status)

    @property
    def immutable(self) -> bool:
        return self.status in {EvaluationStatus.SUCCEEDED.value, EvaluationStatus.FAILED.value}

    def complete(
        self, *, metrics: dict[str, float], report_ref: dict[str, object] | None, now: datetime
    ) -> Evaluation:
        if self.immutable:
            raise EvaluationImmutableError("已完成的评测不可修改")
        parsed = parse_metrics(metrics)
        verdict = compute_verdict(parsed, self.thresholds)
        return replace(
            self,
            status=EvaluationStatus.SUCCEEDED.value,
            metrics=parsed,
            verdict=verdict,
            report_ref=report_ref,
            started_at=self.started_at or now,
            finished_at=now,
            updated_at=now,
        )


def new_evaluation(
    *,
    evaluation_id: UUID,
    project_id: UUID,
    model_version_id: UUID,
    dataset_version_id: UUID,
    thresholds: dict[str, dict[str, float | None]],
    baseline_evaluation_id: UUID | None,
    actor_id: UUID,
    now: datetime,
    baseline_dataset_version_id: UUID | None = None,
) -> Evaluation:
    return Evaluation(
        evaluation_id=evaluation_id,
        project_id=project_id,
        model_version_id=model_version_id,
        dataset_version_id=dataset_version_id,
        thresholds=thresholds,
        baseline_evaluation_id=baseline_evaluation_id,
        baseline_dataset_version_id=baseline_dataset_version_id,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
    )


@dataclass(frozen=True, slots=True)
class InferenceService:
    service_id: UUID
    project_id: UUID
    model_version_id: UUID
    environment: str
    endpoint_url: str
    status: str = InferenceServiceStatus.PENDING
    resource_ref: str | None = None
    config: dict[str, object] | None = None
    health_status: str = HealthStatus.UNKNOWN
    deployed_at: datetime | None = None
    stopped_at: datetime | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("service_id", "project_id", "model_version_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "environment",
            coerce_enum(self.environment, InferenceEnvironment, "environment").value,
        )
        object.__setattr__(
            self, "endpoint_url", text_value(self.endpoint_url, "endpoint_url", max_length=1024)
        )
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, InferenceServiceStatus, "status").value,
        )
        object.__setattr__(
            self,
            "health_status",
            coerce_enum(self.health_status, HealthStatus, "health_status").value,
        )
        object.__setattr__(
            self, "resource_ref", optional_text(self.resource_ref, "resource_ref", max_length=512)
        )
        object.__setattr__(self, "config", dict(self.config or {}))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("inference-service", self.status)


@dataclass(frozen=True, slots=True)
class InferenceTask:
    task_id: UUID
    project_id: UUID
    model_version_id: UUID
    status: str = InferenceTaskStatus.QUEUED
    service_id: UUID | None = None
    dataset_version_id: UUID | None = None
    episode_ids: list[UUID] | None = None
    result_ref: dict[str, object] | None = None
    metrics: dict[str, float] | None = None
    log_ref: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "project_id", "model_version_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self, "status", coerce_enum(self.status, InferenceTaskStatus, "status").value
        )
        for name in ("service_id", "dataset_version_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        episode_ids: list[UUID] = []
        for item in self.episode_ids or []:
            if not isinstance(item, UUID):
                raise DomainValidationError("episode_ids 必须是 UUID 数组")
            if item not in episode_ids:
                episode_ids.append(item)
        object.__setattr__(self, "episode_ids", episode_ids)
        object.__setattr__(self, "metrics", number_map(self.metrics or {}, "metrics"))
        object.__setattr__(self, "log_ref", optional_text(self.log_ref, "log_ref", max_length=1024))
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        return allowed_actions("inference-task", self.status)


def new_inference_task(
    *,
    task_id: UUID,
    project_id: UUID,
    model_version_id: UUID,
    service_id: UUID | None,
    dataset_version_id: UUID | None,
    episode_ids: list[UUID] | None,
    actor_id: UUID,
    now: datetime,
) -> InferenceTask:
    return InferenceTask(
        task_id=task_id,
        project_id=project_id,
        model_version_id=model_version_id,
        service_id=service_id,
        dataset_version_id=dataset_version_id,
        episode_ids=episode_ids or [],
        created_at=now,
        updated_at=now,
        created_by=actor_id,
    )


@dataclass(frozen=True, slots=True)
class ModelFeedback:
    feedback_id: UUID
    project_id: UUID
    model_version_id: UUID
    issue_type: str
    severity: str
    description: str
    dimensions: list[GapDimension]
    status: str = FeedbackStatus.OPEN
    evaluation_id: UUID | None = None
    inference_task_id: UUID | None = None
    evidence_refs: list[str] | None = None
    converted_requirement_id: UUID | None = None
    resolution_note: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("feedback_id", "project_id", "model_version_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self, "issue_type", coerce_enum(self.issue_type, FeedbackIssueType, "issue_type").value
        )
        object.__setattr__(
            self, "severity", coerce_enum(self.severity, FeedbackSeverity, "severity").value
        )
        object.__setattr__(
            self, "description", text_value(self.description, "description", max_length=4000)
        )
        object.__setattr__(self, "dimensions", parse_dimensions(self.dimensions))
        object.__setattr__(self, "status", coerce_enum(self.status, FeedbackStatus, "status").value)
        for name in ("evaluation_id", "inference_task_id", "converted_requirement_id"):
            object.__setattr__(self, name, optional_uuid(getattr(self, name), name))
        object.__setattr__(
            self,
            "evidence_refs",
            string_list(self.evidence_refs, "evidence_refs", max_length=1024),
        )
        object.__setattr__(
            self,
            "resolution_note",
            optional_text(self.resolution_note, "resolution_note", max_length=2000),
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        if self.converted_requirement_id is not None:
            return (
                frozenset({"resolve"})
                if self.status == FeedbackStatus.REQUIREMENT_CREATED.value
                else frozenset()
            )
        return allowed_actions("feedback", self.status)


def new_feedback(
    *,
    feedback_id: UUID,
    project_id: UUID,
    model_version_id: UUID,
    issue_type: str,
    severity: str,
    description: str,
    dimensions: list[GapDimension],
    evaluation_id: UUID | None,
    inference_task_id: UUID | None,
    evidence_refs: list[str] | None,
    actor_id: UUID,
    now: datetime,
) -> ModelFeedback:
    return ModelFeedback(
        feedback_id=feedback_id,
        project_id=project_id,
        model_version_id=model_version_id,
        issue_type=issue_type,
        severity=severity,
        description=description,
        dimensions=dimensions,
        evaluation_id=evaluation_id,
        inference_task_id=inference_task_id,
        evidence_refs=evidence_refs or [],
        created_at=now,
        updated_at=now,
        created_by=actor_id,
    )


@dataclass(frozen=True, slots=True)
class DataRequirement:
    requirement_id: UUID
    project_id: UUID
    code: str
    title: str
    source_type: str
    dimensions: list[GapDimension]
    owner_id: UUID
    target_episode_count: int = 1
    status: str = RequirementStatus.DRAFT
    description: str | None = None
    source_feedback_ids: list[UUID] | None = None
    source_evaluation_ids: list[UUID] | None = None
    quality_thresholds: dict[str, object] | None = None
    priority: str = RequirementPriority.P1
    due_at: datetime | None = None
    linked_dataset_version_ids: list[UUID] | None = None
    retest_evaluation_ids: list[UUID] | None = None
    satisfied_at: datetime | None = None
    satisfaction_evidence_ref: str | None = None
    row_version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("requirement_id", "project_id", "owner_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "code", text_value(self.code, "code", max_length=64))
        object.__setattr__(self, "title", text_value(self.title, "title", max_length=200))
        object.__setattr__(
            self,
            "source_type",
            coerce_enum(self.source_type, RequirementSourceType, "source_type").value,
        )
        object.__setattr__(self, "dimensions", parse_dimensions(self.dimensions))
        object.__setattr__(
            self,
            "target_episode_count",
            positive_int(self.target_episode_count, "target_episode_count"),
        )
        object.__setattr__(
            self, "status", coerce_enum(self.status, RequirementStatus, "status").value
        )
        object.__setattr__(
            self, "description", optional_text(self.description, "description", max_length=4000)
        )
        for name in (
            "source_feedback_ids",
            "source_evaluation_ids",
            "linked_dataset_version_ids",
            "retest_evaluation_ids",
        ):
            raw = getattr(self, name) or []
            values: list[UUID] = []
            for item in raw:
                if not isinstance(item, UUID):
                    raise DomainValidationError(f"{name} 必须是 UUID 数组")
                if item not in values:
                    values.append(item)
            object.__setattr__(self, name, values)
        object.__setattr__(self, "quality_thresholds", dict(self.quality_thresholds or {}))
        object.__setattr__(
            self, "priority", coerce_enum(self.priority, RequirementPriority, "priority").value
        )
        object.__setattr__(self, "due_at", optional_aware_datetime(self.due_at, "due_at"))
        object.__setattr__(
            self, "satisfied_at", optional_aware_datetime(self.satisfied_at, "satisfied_at")
        )
        object.__setattr__(
            self,
            "satisfaction_evidence_ref",
            optional_text(
                self.satisfaction_evidence_ref, "satisfaction_evidence_ref", max_length=1024
            ),
        )
        object.__setattr__(self, "row_version", positive_int(self.row_version, "row_version"))

    @property
    def allowed_actions(self) -> frozenset[str]:
        actions = allowed_actions("requirement", self.status)
        if self.status in {RequirementStatus.SATISFIED.value, RequirementStatus.CLOSED.value}:
            actions = actions | {"link-retest"}
        return actions


def new_requirement(
    *,
    requirement_id: UUID,
    project_id: UUID,
    code: str,
    title: str,
    source_type: str,
    dimensions: list[GapDimension],
    owner_id: UUID,
    target_episode_count: int,
    description: str | None,
    source_feedback_ids: list[UUID] | None,
    source_evaluation_ids: list[UUID] | None,
    quality_thresholds: dict[str, object] | None,
    priority: str,
    due_at: datetime | None,
    actor_id: UUID,
    now: datetime,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=requirement_id,
        project_id=project_id,
        code=code,
        title=title,
        source_type=source_type,
        dimensions=dimensions,
        owner_id=owner_id,
        target_episode_count=target_episode_count,
        description=description,
        source_feedback_ids=source_feedback_ids or [],
        source_evaluation_ids=source_evaluation_ids or [],
        quality_thresholds=quality_thresholds or {},
        priority=priority,
        due_at=due_at,
        created_at=now,
        updated_at=now,
        created_by=actor_id,
        updated_by=actor_id,
    )


__all__ = [
    "DataRequirement",
    "Evaluation",
    "FeedbackSeverity",
    "GapDimension",
    "InferenceService",
    "InferenceTask",
    "Model",
    "ModelFeedback",
    "ModelVersion",
    "EvaluationDatasetNotReadyError",
    "ModelVersionInUseError",
    "new_evaluation",
    "new_feedback",
    "new_inference_task",
    "new_model",
    "new_model_version",
    "new_requirement",
    "compute_verdict",
    "sha256_value",
    "aware_datetime",
    "non_negative_int",
]
