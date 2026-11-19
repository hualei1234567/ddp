"""阶段 5 HTTP 请求模型。

响应模型在 ``modelops_response_models.py``：请求侧统一 ``extra="forbid"``，
响应侧必须完整输出运行时真实字段，两者语义不同，不放在同一文件。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.modelops.enums import FailureCaseCategory, FeedbackSeverity
from app.domain.modelops.failure_cases import MAX_FAILURE_CASES
from app.domain.modelops.gaps import GapDimension


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list)
    payload: dict[str, object] | None = None


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    task_type: str
    framework: str = Field(min_length=1, max_length=100)
    owner_id: UUID
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list)


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    framework: str | None = Field(default=None, min_length=1, max_length=100)
    tags: list[str] | None = None


class ArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_key: str = Field(min_length=1, max_length=1024)
    sha256: str
    size_bytes: int = Field(ge=1)
    format: str


class RuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework: str = Field(min_length=1, max_length=100)
    framework_version: str = Field(min_length=1, max_length=100)
    python_version: str | None = Field(default=None, max_length=32)
    cuda_version: str | None = Field(default=None, max_length=32)
    image_ref: str | None = Field(default=None, max_length=512)


class ModelVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact: ArtifactInput
    runtime: RuntimeInput
    training_request_id: UUID | None = None
    training_delivery_id: UUID | None = None
    external_ref_id: UUID | None = None
    dataset_version_ids: list[UUID] = Field(default_factory=list)
    metrics_summary: dict[str, float] = Field(default_factory=dict)
    release_notes: str | None = Field(default=None, max_length=4000)


class EvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    dataset_version_id: UUID
    metrics: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, dict[str, float | None]]
    baseline_evaluation_id: UUID | None = None
    # 跨数据集版本对比必须显式声明。默认 False：既有同数据集基线行为不变，
    # 跨数据集而未声明则明确拒绝（错误码 evaluation_cross_dataset_not_declared）。
    cross_dataset_comparison: bool = False
    adapter_id: str | None = Field(default=None, min_length=1, max_length=100)
    # 适配器执行参数（如产物 object_key、导出任务）。应用层早已透传，HTTP
    # 层此前没有入口，导致评测只能依赖适配器"单产物回退"，多产物时直接失败。
    config: dict[str, object] = Field(default_factory=dict)


class InferenceServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    environment: str
    endpoint_url: str = Field(min_length=1, max_length=1024)
    resource_ref: str | None = Field(default=None, max_length=512)
    config: dict[str, object] = Field(default_factory=dict)


class InferenceTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    service_id: UUID | None = None
    dataset_version_id: UUID | None = None
    episode_ids: list[UUID] = Field(default_factory=list)
    adapter_id: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, object] = Field(default_factory=dict)


class GapDimensionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_type: str
    dimension_value: str = Field(min_length=1, max_length=200)
    current_count: int | None = Field(default=None, ge=0)
    target_count: int | None = Field(default=None, ge=0)

    def to_domain(self) -> GapDimension:
        return GapDimension(
            dimension_type=self.dimension_type,
            dimension_value=self.dimension_value,
            current_count=self.current_count,
            target_count=self.target_count,
        )


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version_id: UUID
    evaluation_id: UUID | None = None
    inference_task_id: UUID | None = None
    issue_type: str
    severity: str
    description: str = Field(min_length=1, max_length=4000)
    dimensions: list[GapDimensionInput] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class FeedbackActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    resolution_note: str | None = Field(default=None, max_length=2000)
    requirement_code: str | None = Field(default=None, max_length=64)
    requirement_title: str | None = Field(default=None, max_length=200)
    target_episode_count: int | None = Field(default=None, ge=1)
    due_at: datetime | None = None
    priority: str | None = None


class GapCandidateConvertRequest(BaseModel):
    """人工把候选缺口转为 DRAFT 需求的请求。

    刻意**没有** ``dimensions`` / ``source_type`` / ``source_evaluation_ids``：
    维度与来源评测由服务端按当前候选事实推导，调用方不能把 ``suggested_gap``
    提示值直接当权威维度。``code`` / ``title`` / ``owner_id`` 必须由人填写，
    不存在自动生成或自动审批路径。
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    owner_id: UUID
    target_episode_count: int = Field(default=1, ge=1)
    quality_thresholds: dict[str, object] = Field(default_factory=dict)
    priority: str = "P1"
    due_at: datetime | None = None


class RequirementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    source_type: str
    source_feedback_ids: list[UUID] = Field(default_factory=list)
    source_evaluation_ids: list[UUID] = Field(default_factory=list)
    dimensions: list[GapDimensionInput] = Field(min_length=1)
    target_episode_count: int = Field(default=1, ge=1)
    quality_thresholds: dict[str, object] = Field(default_factory=dict)
    priority: str = "P1"
    due_at: datetime | None = None
    owner_id: UUID


class RequirementActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    dataset_version_ids: list[UUID] = Field(default_factory=list)
    evaluation_id: UUID | None = None
    satisfaction_evidence_ref: str | None = Field(default=None, max_length=1024)


class FailureCasePayload(BaseModel):
    """回调中的一条失败用例；字段与契约 ``FailureCase`` 一致。

    枚举与条数在 Pydantic 层先拒绝（非法类别/超过条数 → 既有
    ``validation_failed``）；字节上限与字段细化由领域层
    ``parse_failure_cases`` 兜底，两处口径同源。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    category: FailureCaseCategory
    severity: FeedbackSeverity
    episode_key: str | None = Field(default=None, max_length=200)
    episode_id: UUID | None = None
    evidence: dict[str, object] | str | None = None
    metric: str | None = Field(default=None, max_length=100)
    observed: float | str | None = None
    expected: float | str | None = None
    suggested_gap: str | None = Field(default=None, max_length=200)


class ModelAdapterCallbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=100)
    event_type: str
    project_id: UUID
    evaluation_id: UUID | None = None
    task_id: UUID | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    verdict: str | None = None
    report_ref: dict[str, object] | None = None
    failure_cases: list[FailureCasePayload] = Field(
        default_factory=list, max_length=MAX_FAILURE_CASES
    )
    result_ref: dict[str, object] | None = None
    log_ref: str | None = Field(default=None, max_length=1024)
    error_code: str | None = Field(default=None, max_length=100)
    error_summary: str | None = Field(default=None, max_length=2000)


__all__ = [
    "ActionRequest",
    "ArtifactInput",
    "EvaluationCreate",
    "FailureCasePayload",
    "GapCandidateConvertRequest",
    "FeedbackActionRequest",
    "FeedbackCreate",
    "GapDimensionInput",
    "InferenceServiceCreate",
    "InferenceTaskCreate",
    "ModelAdapterCallbackPayload",
    "ModelCreate",
    "ModelPatch",
    "ModelVersionCreate",
    "RequirementActionRequest",
    "RequirementCreate",
    "RuntimeInput",
]
