"""标注提交、记录、复核与仲裁 HTTP DTO。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.annotation_common import public_actions
from app.domain.annotation.arbitration import AnnotationArbitration
from app.domain.annotation.enums import (
    AnnotationArbitrationStatus,
    AnnotationReviewStatus,
    AnnotationSubmissionStatus,
)
from app.domain.annotation.review import AnnotationReview
from app.domain.annotation.state_machines import (
    allowed_arbitration_actions,
    allowed_review_actions,
    allowed_submission_actions,
)
from app.domain.annotation.submission import AnnotationRecord, AnnotationSubmission

# 提交资源上对外公开的动作。``approve`` 等裁决动作只能经复核 / 仲裁资源执行，
# 直接调用提交动作端点会被拒绝；响应里的 ``allowed_actions`` 必须与这个集合
# 取交集，否则前端会渲染出提交端点不接受的按钮。路由与 DTO 共用这一份。
PUBLIC_SUBMISSION_ACTIONS = frozenset({"submit", "withdraw", "require-rework"})


class AnnotationSubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: UUID
    records: list[dict[str, object]] = Field(min_length=1)
    data_source: str = Field(default="UNKNOWN", max_length=32)


class AnnotationSubmissionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class AnnotationReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    reviewer_id: UUID


class AnnotationSubmissionReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_id: UUID


class AnnotationReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    issues: list[dict[str, object]] | None = None
    checklist: list[dict[str, object]] | None = None
    review_duration_sec: int | None = Field(default=None, ge=0)


class AnnotationArbitrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    arbiter_ids: list[UUID] = Field(min_length=1)
    quorum: int = Field(ge=1)
    trigger: str = Field(max_length=64)
    review_id: UUID | None = None


class AnnotationSubmissionArbitrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arbiter_ids: list[UUID] = Field(min_length=1)
    quorum: int = Field(ge=1)
    trigger: str = Field(max_length=64)
    review_id: UUID | None = None


class AnnotationArbitrationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class AnnotationArbitrationVoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(max_length=16)
    reason: str | None = Field(default=None, max_length=2000)


class AnnotationRecordOut(BaseModel):
    annotation_id: UUID
    annotation_lineage_id: UUID
    annotation_version: int
    project_id: UUID
    submission_id: UUID
    episode_id: UUID
    episode_version: int
    task_id: UUID
    schema_id: UUID
    schema_version: int
    status: str
    time_range: dict[str, object] | None
    labels: list[dict[str, object]]
    evidence_refs: list[dict[str, object]]
    record_sha256: str
    supersedes_id: UUID | None
    approved_by: UUID | None
    approved_at: datetime | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, record: AnnotationRecord) -> AnnotationRecordOut:
        return cls(
            annotation_id=record.annotation_id,
            annotation_lineage_id=record.annotation_lineage_id,
            annotation_version=record.annotation_version,
            project_id=record.project_id,
            submission_id=record.submission_id,
            episode_id=record.episode_id,
            episode_version=record.episode_version,
            task_id=record.task_id,
            schema_id=record.schema_id,
            schema_version=record.schema_version,
            status=_value(record.status),
            time_range=dict(record.time_range) if record.time_range is not None else None,
            labels=[item.to_payload() for item in record.labels],
            evidence_refs=[item.to_payload() for item in record.evidence_refs],
            record_sha256=record.record_sha256 or "",
            supersedes_id=record.supersedes_id,
            approved_by=record.approved_by,
            approved_at=record.approved_at,
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by=record.created_by,
            updated_by=record.updated_by,
        )


class AnnotationSubmissionOut(BaseModel):
    submission_id: UUID
    project_id: UUID
    task_id: UUID
    batch_id: UUID
    assignment_id: UUID
    annotator_id: UUID
    submission_version: int
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    records_digest: str
    item_count: int
    status: str
    allowed_actions: list[str]
    data_source: str
    record_refs: list[UUID]
    records: list[AnnotationRecordOut]
    review_id: UUID | None
    arbitration_id: UUID | None
    supersedes_id: UUID | None
    submitted_at: datetime | None
    decided_at: datetime | None
    reason: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, submission: AnnotationSubmission) -> AnnotationSubmissionOut:
        return cls(
            submission_id=submission.submission_id,
            project_id=submission.project_id,
            task_id=submission.task_id,
            batch_id=submission.batch_id,
            assignment_id=submission.assignment_id,
            annotator_id=submission.annotator_id,
            submission_version=submission.submission_version,
            schema_id=submission.schema_id,
            schema_version=submission.schema_version,
            schema_sha256=submission.schema_sha256,
            records_digest=submission.records_digest,
            item_count=submission.item_count,
            status=_value(submission.status),
            allowed_actions=public_actions(
                allowed_submission_actions(AnnotationSubmissionStatus(_value(submission.status)))
                & PUBLIC_SUBMISSION_ACTIONS
            ),
            data_source=_value(submission.data_source),
            record_refs=list(submission.record_refs),
            records=[AnnotationRecordOut.from_domain(item) for item in submission.records],
            review_id=submission.review_id,
            arbitration_id=submission.arbitration_id,
            supersedes_id=submission.supersedes_id,
            submitted_at=submission.submitted_at,
            decided_at=submission.decided_at,
            reason=submission.reason,
            version=submission.version,
            created_at=submission.created_at,
            updated_at=submission.updated_at,
            created_by=submission.created_by,
            updated_by=submission.updated_by,
        )


class AnnotationReviewOut(BaseModel):
    review_id: UUID
    project_id: UUID
    submission_id: UUID
    task_id: UUID
    reviewer_id: UUID
    review_round: int
    status: str
    allowed_actions: list[str]
    decision: str | None
    issues: list[dict[str, object]]
    checklist: list[dict[str, object]]
    review_duration_sec: int | None
    reason: str | None
    decided_at: datetime | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, review: AnnotationReview) -> AnnotationReviewOut:
        return cls(
            review_id=review.review_id,
            project_id=review.project_id,
            submission_id=review.submission_id,
            task_id=review.task_id,
            reviewer_id=review.reviewer_id,
            review_round=review.review_round,
            status=_value(review.status),
            allowed_actions=public_actions(
                allowed_review_actions(AnnotationReviewStatus(_value(review.status)))
            ),
            decision=_value(review.decision) if review.decision is not None else None,
            issues=[item.to_payload() for item in review.issues],
            checklist=[item.to_payload() for item in review.checklist],
            review_duration_sec=review.review_duration_sec,
            reason=review.reason,
            decided_at=review.decided_at,
            version=review.version,
            created_at=review.created_at,
            updated_at=review.updated_at,
            created_by=review.created_by,
            updated_by=review.updated_by,
        )


class AnnotationArbitrationOut(BaseModel):
    arbitration_id: UUID
    project_id: UUID
    task_id: UUID
    submission_id: UUID
    review_id: UUID | None
    arbiter_ids: list[UUID]
    quorum: int
    trigger: str
    status: str
    allowed_actions: list[str]
    votes: list[dict[str, object]]
    effective_vote_count: int
    final_decision: str | None
    reason: str | None
    resolved_at: datetime | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, arbitration: AnnotationArbitration) -> AnnotationArbitrationOut:
        return cls(
            arbitration_id=arbitration.arbitration_id,
            project_id=arbitration.project_id,
            task_id=arbitration.task_id,
            submission_id=arbitration.submission_id,
            review_id=arbitration.review_id,
            arbiter_ids=list(arbitration.arbiter_ids),
            quorum=arbitration.quorum,
            trigger=_value(arbitration.trigger),
            status=_value(arbitration.status),
            allowed_actions=public_actions(
                allowed_arbitration_actions(AnnotationArbitrationStatus(_value(arbitration.status)))
            ),
            votes=[item.to_payload() for item in arbitration.votes],
            effective_vote_count=arbitration.effective_vote_count,
            final_decision=(
                _value(arbitration.final_decision)
                if arbitration.final_decision is not None
                else None
            ),
            reason=arbitration.reason,
            resolved_at=arbitration.resolved_at,
            version=arbitration.version,
            created_at=arbitration.created_at,
            updated_at=arbitration.updated_at,
            created_by=arbitration.created_by,
            updated_by=arbitration.updated_by,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
