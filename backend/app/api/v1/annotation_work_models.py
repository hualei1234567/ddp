"""标注任务、批次与分配 HTTP DTO。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.annotation_common import public_actions
from app.domain.annotation.assignment import AnnotationAssignment
from app.domain.annotation.batch import AnnotationBatch
from app.domain.annotation.enums import (
    AnnotationAssignmentStatus,
    AnnotationBatchStatus,
    AnnotationTaskStatus,
)
from app.domain.annotation.review_policy import ReviewPolicy
from app.domain.annotation.state_machines import (
    allowed_assignment_actions,
    allowed_batch_actions,
    allowed_task_actions,
)
from app.domain.annotation.task import AnnotationTask, is_overdue
from app.domain.annotation.workload import WorkloadSummary


class AnnotationTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    sample_set_id: UUID
    schema_id: UUID
    schema_version: int | None = Field(default=None, ge=1)
    target_item_count: int = Field(ge=1)
    target_episode_count: int = Field(ge=1)
    due_at: datetime | None = None
    priority: str = Field(default="P1", max_length=8)
    assignment_mode: str = Field(default="MANUAL", max_length=16)
    iaa_policy: dict[str, object] = Field(default_factory=dict)
    quality_policy: dict[str, object] = Field(default_factory=dict)
    # 双标策略的便捷字段；严格校验由 ReviewPolicy 统一完成。
    review_mode: str = Field(default="SINGLE", max_length=8)
    agreement_threshold: float | None = Field(default=None, ge=0, le=1)
    auto_approve: bool = False

    def merged_iaa_policy(self) -> dict[str, object]:
        policy = dict(self.iaa_policy)
        explicit = self.model_fields_set
        if "review_mode" in explicit:
            policy["review_mode"] = self.review_mode
        if "agreement_threshold" in explicit:
            policy["agreement_threshold"] = self.agreement_threshold
        if "auto_approve" in explicit:
            policy["auto_approve"] = self.auto_approve
        ReviewPolicy.from_mapping(policy)
        return policy


class AnnotationTaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=2000)
    target_item_count: int | None = Field(default=None, ge=1)
    target_episode_count: int | None = Field(default=None, ge=1)
    due_at: datetime | None = None
    priority: str | None = Field(default=None, max_length=8)
    assignment_mode: str | None = Field(default=None, max_length=16)
    iaa_policy: dict[str, object] | None = None
    quality_policy: dict[str, object] | None = None


class AnnotationTaskActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    waiver_reason: str | None = Field(default=None, max_length=2000)


class AnnotationBatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planned_item_count: int = Field(ge=0)
    code: str | None = Field(default=None, max_length=64)
    sequence_no: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, max_length=200)
    stratum: str | None = Field(default=None, max_length=100)
    item_refs: list[UUID] = Field(default_factory=list)
    due_at: datetime | None = None


class AnnotationBatchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    stratum: str | None = Field(default=None, max_length=100)
    item_refs: list[UUID] | None = None
    planned_item_count: int | None = Field(default=None, ge=0)
    due_at: datetime | None = None


class AnnotationBatchActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)


class AnnotationAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    assignee_id: UUID
    assignment_role: str = Field(default="ANNOTATOR", max_length=16)
    planned_item_count: int = Field(default=0, ge=0)
    planned_minutes: int = Field(default=0, ge=0)
    lease_expires_at: datetime | None = None


class AnnotationBatchAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: UUID
    assignment_role: str = Field(default="ANNOTATOR", max_length=16)
    planned_item_count: int = Field(default=0, ge=0)
    planned_minutes: int = Field(default=0, ge=0)
    lease_expires_at: datetime | None = None


class AnnotationAssignmentActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=2000)
    actual_minutes: int | None = Field(default=None, ge=0)
    submitted_item_count: int | None = Field(default=None, ge=0)
    approved_item_count: int | None = Field(default=None, ge=0)


class AnnotationTaskOut(BaseModel):
    task_id: UUID
    project_id: UUID
    code: str
    name: str
    description: str | None
    sample_set_id: UUID
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    status: str
    # 服务端算好的可执行动作，前端只按它渲染按钮；系统内部动作不会出现。
    allowed_actions: list[str]
    target_item_count: int
    target_episode_count: int
    priority: str
    assignment_mode: str
    # 从 iaa_policy 派生：SINGLE / DOUBLE，前端据此展示双标门槛与一致性结论。
    review_mode: str
    iaa_policy: dict[str, object]
    quality_policy: dict[str, object]
    progress: dict[str, object]
    quality_summary: dict[str, object]
    due_at: datetime | None
    # 派生事实：由服务端按当前时刻计算，不在库中落状态、不触发改派。
    is_overdue: bool
    accepted_at: datetime | None
    completed_at: datetime | None
    gold_set_id: UUID | None
    iaa_report_id: UUID | None
    status_reason: str | None
    blocked_reason: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(
        cls,
        task: AnnotationTask,
        *,
        now: datetime | None = None,
    ) -> AnnotationTaskOut:
        return cls(
            task_id=task.task_id,
            project_id=task.project_id,
            code=task.code,
            name=task.name,
            description=task.description,
            sample_set_id=task.sample_set_id,
            schema_id=task.schema_id,
            schema_version=task.schema_version,
            schema_sha256=task.schema_sha256,
            status=_value(task.status),
            allowed_actions=public_actions(
                allowed_task_actions(AnnotationTaskStatus(_value(task.status)))
            ),
            target_item_count=task.target_item_count,
            target_episode_count=task.target_episode_count,
            priority=_value(task.priority),
            assignment_mode=_value(task.assignment_mode),
            review_mode=task.review_mode.value,
            iaa_policy=dict(task.iaa_policy),
            quality_policy=dict(task.quality_policy),
            progress=dict(task.progress),
            quality_summary=dict(task.quality_summary),
            due_at=task.due_at,
            is_overdue=is_overdue(task, now or datetime.now(UTC)),
            accepted_at=task.accepted_at,
            completed_at=task.completed_at,
            gold_set_id=task.gold_set_id,
            iaa_report_id=task.iaa_report_id,
            status_reason=task.status_reason,
            blocked_reason=task.blocked_reason,
            version=task.version,
            created_at=task.created_at,
            updated_at=task.updated_at,
            created_by=task.created_by,
            updated_by=task.updated_by,
        )


class AnnotationBatchOut(BaseModel):
    batch_id: UUID
    project_id: UUID
    task_id: UUID
    code: str
    sequence_no: int
    status: str
    allowed_actions: list[str]
    name: str | None
    stratum: str | None
    item_refs: list[UUID]
    planned_item_count: int
    assignee_ids: list[UUID]
    reviewer_pool_ids: list[UUID]
    due_at: datetime | None
    submitted_item_count: int
    approved_item_count: int
    rejected_item_count: int
    rework_count: int
    quality_summary: dict[str, object]
    status_reason: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, batch: AnnotationBatch) -> AnnotationBatchOut:
        return cls(
            batch_id=batch.batch_id,
            project_id=batch.project_id,
            task_id=batch.task_id,
            code=batch.code,
            sequence_no=batch.sequence_no,
            status=_value(batch.status),
            allowed_actions=public_actions(
                allowed_batch_actions(AnnotationBatchStatus(_value(batch.status)))
            ),
            name=batch.name,
            stratum=batch.stratum,
            item_refs=list(batch.item_refs),
            planned_item_count=batch.planned_item_count,
            assignee_ids=list(batch.assignee_ids),
            reviewer_pool_ids=list(batch.reviewer_pool_ids),
            due_at=batch.due_at,
            submitted_item_count=batch.submitted_item_count,
            approved_item_count=batch.approved_item_count,
            rejected_item_count=batch.rejected_item_count,
            rework_count=batch.rework_count,
            quality_summary=dict(batch.quality_summary),
            status_reason=batch.status_reason,
            version=batch.version,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
            created_by=batch.created_by,
            updated_by=batch.updated_by,
        )


class AnnotationAssignmentOut(BaseModel):
    assignment_id: UUID
    project_id: UUID
    task_id: UUID
    batch_id: UUID
    assignee_id: UUID
    status: str
    allowed_actions: list[str]
    assignment_role: str
    planned_item_count: int
    claimed_item_count: int
    submitted_item_count: int
    approved_item_count: int
    planned_minutes: int
    actual_minutes: int | None
    lease_expires_at: datetime | None
    claimed_at: datetime | None
    submitted_at: datetime | None
    completed_at: datetime | None
    revoked_reason: str | None
    status_reason: str | None
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    created_by: UUID | None
    updated_by: UUID | None

    @classmethod
    def from_domain(cls, assignment: AnnotationAssignment) -> AnnotationAssignmentOut:
        return cls(
            assignment_id=assignment.assignment_id,
            project_id=assignment.project_id,
            task_id=assignment.task_id,
            batch_id=assignment.batch_id,
            assignee_id=assignment.assignee_id,
            status=_value(assignment.status),
            allowed_actions=public_actions(
                allowed_assignment_actions(AnnotationAssignmentStatus(_value(assignment.status)))
            ),
            assignment_role=_value(assignment.assignment_role),
            planned_item_count=assignment.planned_item_count,
            claimed_item_count=assignment.claimed_item_count,
            submitted_item_count=assignment.submitted_item_count,
            approved_item_count=assignment.approved_item_count,
            planned_minutes=assignment.planned_minutes,
            actual_minutes=assignment.actual_minutes,
            lease_expires_at=assignment.lease_expires_at,
            claimed_at=assignment.claimed_at,
            submitted_at=assignment.submitted_at,
            completed_at=assignment.completed_at,
            revoked_reason=assignment.revoked_reason,
            status_reason=assignment.status_reason,
            version=assignment.version,
            created_at=assignment.created_at,
            updated_at=assignment.updated_at,
            created_by=assignment.created_by,
            updated_by=assignment.updated_by,
        )


class AnnotationWorkloadOut(BaseModel):
    project_id: UUID
    user_id: UUID
    period_start: datetime
    period_end: datetime
    assigned_item_count: int
    claimed_item_count: int
    submitted_item_count: int
    approved_item_count: int
    rework_item_count: int
    total_minutes: int
    capacity_minutes: int | None
    utilization_ratio: float | None
    status: str
    missing_semantics: str
    source_version: str

    @classmethod
    def from_domain(cls, workload: WorkloadSummary) -> AnnotationWorkloadOut:
        return cls(
            project_id=workload.project_id,
            user_id=workload.user_id,
            period_start=workload.period_start,
            period_end=workload.period_end,
            assigned_item_count=workload.assigned_item_count,
            claimed_item_count=workload.claimed_item_count,
            submitted_item_count=workload.submitted_item_count,
            approved_item_count=workload.approved_item_count,
            rework_item_count=workload.rework_item_count,
            total_minutes=workload.total_minutes,
            capacity_minutes=workload.capacity_minutes,
            utilization_ratio=workload.utilization_ratio,
            status=_value(workload.status),
            missing_semantics=_value(workload.missing_semantics),
            source_version=workload.source_version,
        )


def _value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)
