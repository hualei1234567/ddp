"""黄金集、质检抽检与 IAA 报告 HTTP DTO。

黄金集默认响应只使用脱敏视图；包含 ``expected_labels`` 的答案视图仅由
显式授权端点返回，并且必须留下审计记录。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.v1.annotation_common import public_actions
from app.application.annotation.ports import GoldEvaluationRecord, IAAReportRecord
from app.domain.annotation.enums import GoldSetStatus, QualitySampleStatus
from app.domain.annotation.quality_sample import QualitySample
from app.domain.annotation.state_machines import (
    allowed_gold_set_actions,
    allowed_quality_sample_actions,
)


class GoldSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    schema_id: UUID
    schema_version: int | None = Field(default=None, ge=1)
    schema_sha256: str | None = Field(default=None, max_length=80)
    items: list[dict[str, object]] = Field(min_length=1)
    exposure_policy: str = Field(default="HIDDEN", max_length=32)
    max_exposure_per_annotator: int = Field(default=1, ge=1)
    rotation_policy: str = Field(default="NONE", max_length=32)
    usage_policy: dict[str, object] = Field(default_factory=dict)


class GoldSetActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class GoldEvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    submission_id: UUID


class GoldInjectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    annotator_id: UUID
    item_ids: list[UUID] = Field(min_length=1)


class QualitySampleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    submission_id: UUID
    annotator_id: UUID
    sampling_policy_version: str = Field(min_length=1, max_length=64)
    sample_type: str = Field(max_length=32)
    sample_reason: str = Field(min_length=1, max_length=500)
    item_refs: list[UUID] = Field(default_factory=list)
    stratum: str | None = Field(default=None, max_length=100)
    due_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class QualitySamplePlanCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    stratum: str | None = Field(default=None, max_length=100)
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)


class QualitySamplePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    candidates: list[QualitySamplePlanCandidate] = Field(min_length=1, max_length=500)
    sampling_policy_version: str = Field(min_length=1, max_length=64)
    sample_type: str = Field(min_length=1, max_length=16)
    sample_size: int = Field(ge=1)
    sample_reason: str = Field(min_length=1, max_length=500)
    # STRATIFIED 抽样时指定目标分层；候选自身 stratum 用于落库与排序。
    stratum: str | None = Field(default=None, max_length=100)
    risk_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    due_at: datetime | None = None

    @field_validator("candidates")
    @classmethod
    def _unique_submissions(
        cls,
        value: list[QualitySamplePlanCandidate],
    ) -> list[QualitySamplePlanCandidate]:
        seen = {item.submission_id for item in value}
        if len(seen) != len(value):
            raise ValueError("candidates 中的 submission_id 不得重复")
        return value


class QualitySampleActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)


class IAAReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    metric_definitions_version: str = Field(min_length=1, max_length=64)
    iou_threshold: float = Field(default=0.5, ge=0, le=1)


class GoldSetOut(BaseModel):
    """脱敏黄金集视图：items 内不含 expected_labels。"""

    gold_set_id: UUID
    project_id: UUID
    code: str
    name: str | None
    status: str
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    blind: bool
    exposure_policy: str
    max_exposure_per_annotator: int
    rotation_policy: str
    items: list[dict[str, object]]
    version: int
    allowed_actions: list[str]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> GoldSetOut:
        """由领域脱敏视图构造响应。

        ``allowed_actions`` 只能由服务端按状态计算；``redacted_view()`` 是
        领域层的既有单一来源，这里只补这一个字段，不改动脱敏语义。
        """
        return cls(
            **payload,
            allowed_actions=public_actions(
                allowed_gold_set_actions(GoldSetStatus(str(payload["status"])))
            ),
        )


class GoldSetAnswerOut(GoldSetOut):
    """授权答案视图；仅由权限校验通过的端点使用。"""


class GoldEvaluationOut(BaseModel):
    evaluation_id: UUID
    project_id: UUID
    gold_set_id: UUID
    task_id: UUID
    submission_id: UUID
    evaluated_count: int
    passed_count: int
    accuracy: float | None
    status: str
    sample_size: int
    results: list[dict[str, object]]
    computed_at: datetime

    @classmethod
    def from_record(cls, record: GoldEvaluationRecord) -> GoldEvaluationOut:
        payload = record.evaluation.to_payload()
        return cls(
            evaluation_id=record.evaluation_id,
            project_id=record.project_id,
            gold_set_id=record.gold_set_id,
            task_id=record.task_id,
            submission_id=record.submission_id,
            evaluated_count=record.evaluation.evaluated_count,
            passed_count=record.evaluation.passed_count,
            accuracy=record.evaluation.accuracy,
            status=record.evaluation.status,
            sample_size=record.evaluation.sample_size,
            results=payload["results"],  # type: ignore[arg-type]
            computed_at=record.computed_at,
        )


class GoldInjectionOut(BaseModel):
    gold_set_id: UUID
    items: list[dict[str, object]]
    exposure_ids: list[str]


class QualitySampleOut(BaseModel):
    quality_sample_id: UUID
    project_id: UUID
    task_id: UUID
    submission_id: UUID
    annotator_id: UUID
    sampling_policy_version: str
    sample_type: str
    sample_reason: str
    status: str
    allowed_actions: list[str]
    stratum: str | None
    item_refs: list[UUID]
    reviewer_id: UUID | None
    review_id: UUID | None
    arbitration_id: UUID | None
    due_at: datetime | None
    outcome: str | None
    reason: str | None
    closed_at: datetime | None
    metadata: dict[str, object]
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, sample: QualitySample) -> QualitySampleOut:
        return cls(
            quality_sample_id=sample.quality_sample_id,
            project_id=sample.project_id,
            task_id=sample.task_id,
            submission_id=sample.submission_id,
            annotator_id=sample.annotator_id,
            sampling_policy_version=sample.sampling_policy_version,
            sample_type=_value(sample.sample_type),
            sample_reason=sample.sample_reason,
            status=_value(sample.status),
            allowed_actions=public_actions(
                allowed_quality_sample_actions(QualitySampleStatus(_value(sample.status)))
            ),
            stratum=sample.stratum,
            item_refs=list(sample.item_refs),
            reviewer_id=sample.reviewer_id,
            review_id=sample.review_id,
            arbitration_id=sample.arbitration_id,
            due_at=sample.due_at,
            outcome=_value(sample.outcome) if sample.outcome is not None else None,
            reason=sample.reason,
            closed_at=sample.closed_at,
            metadata=dict(sample.metadata),
            version=sample.version,
            created_at=sample.created_at,
            updated_at=sample.updated_at,
            created_by=sample.created_by,
            updated_by=sample.updated_by,
        )


class IAAReportOut(BaseModel):
    report_id: UUID
    project_id: UUID
    task_id: UUID
    schema_id: UUID
    schema_version: int
    status: str
    metric_definitions_version: str
    sample_item_count: int
    overlap_item_count: int
    annotator_count: int
    metrics: list[dict[str, object]]
    excluded_items: list[dict[str, object]]
    computed_at: datetime | None
    version: int

    @classmethod
    def from_record(cls, record: IAAReportRecord) -> IAAReportOut:
        return cls(
            report_id=record.report_id,
            project_id=record.project_id,
            task_id=record.task_id,
            schema_id=record.schema_id,
            schema_version=record.schema_version,
            status=record.status,
            metric_definitions_version=record.metric_definitions_version,
            sample_item_count=record.sample_item_count,
            overlap_item_count=record.overlap_item_count,
            annotator_count=record.annotator_count,
            metrics=[dict(item) for item in record.metrics],
            excluded_items=[dict(item) for item in record.excluded_items],
            computed_at=record.computed_at,
            version=record.version,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
